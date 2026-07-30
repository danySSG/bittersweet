"""«Обсерватория» — аналитический этаж портрета (SPEC v0.11 §A).

Считается ИЗ СОХРАНЁННОГО payload портрета: points[].features лежат там
с v0.4, поэтому старые портреты работают без пересчёта аудио. Каждая
модель — чистая функция от DataFrame/массивов; build_observatory(payload)
собирает всё в готовый к отрисовке dict (фронт только рисует SVG).

Сборка ленивая и одноразовая: роут кладёт результат в payload портрета
(store.save_observatory) и дальше отдаёт из него. t-SNE на сотнях точек
занимает секунды — длительность логируется. Все модели детерминированы
(фиксированные random_state), поэтому гонка двух первых запросов
безобидна: оба посчитают одно и то же.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from time import perf_counter

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.manifold import TSNE
from sklearn.preprocessing import StandardScaler

from app.engine.portrait import BITTERSWEET_THRESHOLD, MOOD_FEATURES, _parse_liked_at

log = logging.getLogger(__name__)

# версия схемы observatory в payload: несовпадение = пересчитать
OBSERVATORY_VERSION = 1

# кварто-квинтовый порядок позиций колеса тональностей (SPEC v0.11 §A1)
FIFTHS_ORDER = ["C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F"]

# тональность и лад точки лежат в meta-строке: «144bpm · B minor»
_META_KEY_RE = re.compile(r"·\s*([A-G]#?)\s+(major|minor)\s*$")

# риджлайны — 4 признака в порядке отрисовки (SPEC v0.11 §A2)
RIDGELINE_FEATURES = ["tempo", "brightness", "minor_score", "bittersweet"]
KDE_GRID_POINTS = 64

# радар (SPEC v0.11 §A3): «средний» контур базы — условная середина
RADAR_AVG_PCT = 50

# человеческие имена осей
FEATURE_RU = {
    "tempo": "темп",
    "minor_score": "минорность",
    "brightness": "яркость",
    "bittersweet": "биттерсвит",
    "percussive": "ритмичность",
    "energy_rms": "плотность",
    "onset_rate": "дробность",
}

# интерпретация знака loading'а в PCA (SPEC v0.11 §A4): (положительное, антоним)
PCA_WORDS = {
    "tempo": ("быстрее", "медленнее"),
    "minor_score": ("минорнее", "мажорнее"),
    "brightness": ("ярче", "тёмнее"),
    "bittersweet": ("биттерсвитнее", "однозначнее"),
    "percussive": ("ритмичнее", "плавнее"),
    "energy_rms": ("плотнее", "воздушнее"),
    "onset_rate": ("дробнее", "протяжнее"),
}
PCA_TOP_LOADINGS = 3

TSNE_RANDOM_STATE = 42

# белые вороны (SPEC v0.11 §A6)
OUTLIER_CONTAMINATION = 0.05
OUTLIERS_TOP = 8
OUTLIER_WHY_FEATURES = 2
# словарь «экстремальности» признака: (при z > 0, при z < 0)
OUTLIER_WORDS = {
    "tempo": ("экстремально быстрый", "экстремально медленный"),
    "minor_score": ("экстремально минорный", "экстремально мажорный"),
    "brightness": ("экстремально яркий", "экстремально тёмный"),
    "bittersweet": ("экстремально биттерсвитный", "экстремально однозначный"),
    "percussive": ("экстремально ритмичный", "экстремально плавный"),
    "energy_rms": ("экстремально плотный", "экстремально воздушный"),
    "onset_rate": ("экстремально дробный", "экстремально протяжный"),
}


def _fmt_value(feature: str, v: float) -> str:
    """Значение признака по-человечески: «222 bpm», «4801 Гц», «0.95»."""
    if feature == "tempo":
        return f"{round(v)} bpm"
    if feature == "brightness":
        return f"{round(v)} Гц"
    if feature == "onset_rate":
        return f"{v:.1f} атак/с"
    return f"{v:.2f}"


# часы вкуса (SPEC v0.11 §A7)
CLOCK_MIN_DATED = 50        # меньше датированных точек — панели нет (null)
CLOCK_MIN_PER_BUCKET = 3    # доля биттерсвита в бакете < 3 треков -> null
CLOCK_WINDOW_HOURS = 3      # скользящее окно инсайта
CLOCK_WINDOW_MIN_TRACKS = 10  # окно тоньше — инсайт не делаем

# энтропия (SPEC v0.11 §A8): границы вердикта в битах
ENTROPY_MONOLITH_MAX = 1.5
ENTROPY_FOCUSED_MAX = 2.2


def _r(x: float, nd: int = 3) -> float:
    """float(round(...)): numpy-скаляры не должны утекать в JSON."""
    return round(float(x), nd)


def points_frame(points: list[dict]) -> pd.DataFrame:
    """DataFrame из points[] сохранённого payload портрета.

    Колонки: MOOD_FEATURES (float, из p["features"]) + label, videoId,
    cluster, meta, liked_at (nullable). ValueError, если точек нет или
    у какой-то нет полных features (портрет до v0.4) — роут отвечает 409.
    """
    if not points:
        raise ValueError("в портрете нет точек")
    rows = []
    for p in points:
        ft = p.get("features")
        if not isinstance(ft, dict):
            raise ValueError("в точках нет признаков (портрет до v0.4)")
        try:
            row = {f: float(ft[f]) for f in MOOD_FEATURES}
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"неполные признаки в точке: {e}") from e
        row["label"] = p.get("label")
        row["videoId"] = p.get("videoId")
        row["cluster"] = p.get("cluster")
        row["meta"] = p.get("meta") or ""
        row["liked_at"] = p.get("liked_at")
        rows.append(row)
    return pd.DataFrame(rows)


def _standardized(df: pd.DataFrame) -> np.ndarray:
    """Стандартизованная матрица MOOD_FEATURES (общая для PCA/t-SNE/леса)."""
    return StandardScaler().fit_transform(df[MOOD_FEATURES].to_numpy(float))


# ------------------------------------------------------------------ модели

def key_wheel(metas: Iterable[str]) -> dict:
    """Колесо тональностей по кругу квинт (SPEC v0.11 §A1).

    Позиции — кварто-квинтовый порядок FIFTHS_ORDER (C G D A E B F# C# G#
    D# A# F). Тональность и лад парсятся из meta-строки точки
    («144bpm · B minor»); точки без тональности не учитываются (n_keyed).
    counts — целые по 12 позициям для мажора и минора; share — доля от
    n_keyed (3 знака); top — самая частая пара (key, mode) или None.
    """
    pos = {note: i for i, note in enumerate(FIFTHS_ORDER)}
    major = [0] * 12
    minor = [0] * 12
    for meta in metas:
        m = _META_KEY_RE.search(str(meta))
        if not m:
            continue
        note, mode = m.group(1), m.group(2)
        (major if mode == "major" else minor)[pos[note]] += 1
    n_keyed = sum(major) + sum(minor)
    top = None
    if n_keyed:
        counts = {(note, "major"): major[i] for i, note in enumerate(FIFTHS_ORDER)}
        counts |= {(note, "minor"): minor[i] for i, note in enumerate(FIFTHS_ORDER)}
        (key, mode), count = max(counts.items(), key=lambda kv: kv[1])
        top = {"key": key, "mode": mode, "count": int(count),
               "share": _r(count / n_keyed)}
    share = (lambda c: _r(c / n_keyed) if n_keyed else 0.0)
    return {
        "order": list(FIFTHS_ORDER),
        "major_counts": major,
        "minor_counts": minor,
        "major_share": [share(c) for c in major],
        "minor_share": [share(c) for c in minor],
        "n_keyed": n_keyed,
        "top": top,
    }


def kde_profile(values: Sequence[float], grid_points: int = KDE_GRID_POINTS) -> dict:
    """KDE-профиль признака гауссовым ядром (SPEC v0.11 §A2).

    Bandwidth по Сильверману: bw = 0.9 * min(sigma, IQR/1.34) * n^(-1/5);
    если одна из оценок разброса нулевая — берётся другая, совсем
    константные данные дают плоский профиль (bw -> eps). Сетка —
    grid_points равномерных точек на [p1, p99]. density нормирована
    к максимуму 1. Выход: {grid, density, median, p25, p75}.
    """
    v = np.asarray(values, float)
    n = len(v)
    lo, hi = np.percentile(v, [1, 99])
    if hi <= lo:  # вырожденный диапазон (все значения почти равны)
        lo, hi = lo - 0.5, hi + 0.5
    grid = np.linspace(lo, hi, grid_points)

    sigma = float(v.std(ddof=1)) if n > 1 else 0.0
    p25, p75 = np.percentile(v, [25, 75])
    iqr_sigma = float(p75 - p25) / 1.34
    spreads = [s for s in (sigma, iqr_sigma) if s > 0]
    bw = 0.9 * min(spreads) * n ** (-1 / 5) if spreads else 0.0
    bw = max(bw, 1e-9)

    density = np.exp(-0.5 * ((grid[:, None] - v[None, :]) / bw) ** 2).sum(axis=1)
    peak = float(density.max())
    if peak > 0:
        density = density / peak
    return {
        "grid": [_r(g) for g in grid],
        "density": [_r(d) for d in density],
        "median": _r(np.median(v)),
        "p25": _r(p25),
        "p75": _r(p75),
    }


def ridgelines(df: pd.DataFrame) -> list[dict]:
    """Риджлайны (joyplot) по RIDGELINE_FEATURES (SPEC v0.11 §A2).

    На каждый признак — kde_profile + имя оси; порядок списка = порядок
    отрисовки (tempo, brightness, minor_score, bittersweet).
    """
    return [
        {"feature": f, "name_ru": FEATURE_RU[f], **kde_profile(df[f].to_numpy(float))}
        for f in RIDGELINE_FEATURES
    ]


def radar(df: pd.DataFrame, reference: dict[str, Sequence[float]] | None) -> dict:
    """Радар 7 осей MOOD_FEATURES (SPEC v0.11 §A3).

    self_pct — перцентиль МЕДИАНЫ портрета относительно reference-значений
    признака (медианы всех сохранённых портретов; при <5 портретах роут
    подставляет значения демо-набора). Midrank-перцентиль:
    100 * (ниже + 0.5 * равных) / n, 1 знак. Пустой/отсутствующий
    reference по оси -> честная середина self_pct = 50.
    avg_pct = RADAR_AVG_PCT (50) — контур «среднего» по определению.
    """
    axes = []
    for f in MOOD_FEATURES:
        med = float(df[f].median())
        ref = np.asarray((reference or {}).get(f) or [], float)
        if ref.size:
            pct = 100 * ((ref < med).sum() + 0.5 * (ref == med).sum()) / ref.size
        else:
            pct = 50.0
        axes.append({
            "feature": f,
            "name_ru": FEATURE_RU[f],
            "self_pct": _r(pct, 1),
            "avg_pct": RADAR_AVG_PCT,
        })
    return {"axes": axes}


def _axis_labels(loadings: pd.Series) -> tuple[str, str]:
    """label_pos/label_neg оси PCA из топ-3 |loadings| (SPEC v0.11 §A4).

    Знак веса выбирает слово из PCA_WORDS: положительный — «быстрее»,
    отрицательный — антоним «медленнее»; для label_neg слова зеркалятся.
    """
    top = loadings.abs().sort_values(ascending=False).index[:PCA_TOP_LOADINGS]
    pos = [PCA_WORDS[f][0 if loadings[f] > 0 else 1] for f in top]
    neg = [PCA_WORDS[f][1 if loadings[f] > 0 else 0] for f in top]
    return " · ".join(pos), " · ".join(neg)


def pca_axes(df: pd.DataFrame) -> dict:
    """PCA: 2 компоненты на стандартизованных MOOD_FEATURES (SPEC v0.11 §A4).

    explained — % объяснённой дисперсии по компонентам (убывает по
    построению PCA); loadings — веса признаков (3 знака);
    label_pos/label_neg — интерпретация топ-3 |loadings| (см. _axis_labels).
    Координаты точек НЕ отдаём: биплот проецирует фронт по loadings.
    """
    X = _standardized(df)
    pca = PCA(n_components=2, random_state=0).fit(X)
    axes = []
    for comp in pca.components_:
        loadings = pd.Series(comp, index=MOOD_FEATURES)
        label_pos, label_neg = _axis_labels(loadings)
        axes.append({
            "loadings": {f: _r(w) for f, w in loadings.items()},
            "label_pos": label_pos,
            "label_neg": label_neg,
        })
    return {
        "explained": [_r(v * 100, 1) for v in pca.explained_variance_ratio_],
        "axes": axes,
    }


def tsne_coords(df: pd.DataFrame) -> dict:
    """t-SNE 2D на стандартизованных MOOD_FEATURES (SPEC v0.11 §A5).

    perplexity = min(30, max(5, n//4)) (и всегда < n), random_state=42,
    init="pca". Каждая ось нормируется в 0..100 независимо (вырожденная
    ось -> 50). Порядок и длина x/y = points[] портрета: фронт берёт
    цвета/лейблы/videoId по индексу.
    """
    X = _standardized(df)
    n = len(X)
    perplexity = min(30, max(5, n // 4))
    perplexity = min(perplexity, n - 1)  # требование sklearn: perplexity < n
    xy = TSNE(
        n_components=2, perplexity=perplexity,
        random_state=TSNE_RANDOM_STATE, init="pca",
    ).fit_transform(X)

    def norm(col: np.ndarray) -> list[float]:
        span = float(col.max() - col.min())
        if span <= 0:
            return [50.0] * len(col)
        return [_r(100 * (c - col.min()) / span, 2) for c in col]

    return {"x": norm(xy[:, 0]), "y": norm(xy[:, 1])}


def outliers(df: pd.DataFrame) -> list[dict]:
    """«Белые вороны»: IsolationForest (SPEC v0.11 §A6).

    IsolationForest(contamination=0.05, random_state=42) на
    стандартизованных MOOD_FEATURES. Берутся точки с predict = -1,
    сортировка по аномальности (score_samples, ниже = аномальнее),
    максимум OUTLIERS_TOP (8). score 0..100 — нормированный ранг
    аномальности среди ВСЕХ точек (самая аномальная -> 100).
    why — топ-2 признака по |z| внутри портрета, по-русски со значением:
    «экстремально быстрый (222 bpm)».
    """
    X = _standardized(df)
    n = len(X)
    forest = IsolationForest(
        contamination=OUTLIER_CONTAMINATION, random_state=42,
    ).fit(X)
    anomaly = -forest.score_samples(X)          # больше = аномальнее
    ranks = anomaly.argsort().argsort()          # 0 = самая обычная
    flagged = np.flatnonzero(forest.predict(X) == -1)
    top = sorted(flagged, key=lambda i: -anomaly[i])[:OUTLIERS_TOP]

    out = []
    for i in top:
        z = pd.Series(X[i], index=MOOD_FEATURES)
        why_feats = z.abs().sort_values(ascending=False).index[:OUTLIER_WHY_FEATURES]
        why = [
            f"{OUTLIER_WORDS[f][0 if z[f] > 0 else 1]}"
            f" ({_fmt_value(f, float(df[f].iloc[i]))})"
            for f in why_feats
        ]
        out.append({
            "label": df["label"].iloc[i],
            "videoId": df["videoId"].iloc[i] if pd.notna(df["videoId"].iloc[i]) else None,
            "score": int(round(100 * ranks[i] / (n - 1))) if n > 1 else 100,
            "why": why,
        })
    return out


def clock(df: pd.DataFrame) -> dict | None:
    """«Часы вкуса» по liked_at (SPEC v0.11 §A7) или None (< 50 дат).

    Часы и дни недели — из самих ISO-меток (UTC источника; локальную зону
    пользователя бэкенд не знает). Биттерсвит-трек — по портретному
    определению: bittersweet >= BITTERSWEET_THRESHOLD и brightness выше
    медианы библиотеки. Доли в бакетах с < CLOCK_MIN_PER_BUCKET (3)
    треками -> null. weekday: 0 = понедельник. insight — максимум доли
    биттерсвита в ЦИКЛИЧЕСКОМ скользящем окне 3 часа, только если в окне
    >= CLOCK_WINDOW_MIN_TRACKS (10) треков и доля > 0; иначе null.
    """
    liked = _parse_liked_at(df)
    dated = liked.notna()
    if int(dated.sum()) < CLOCK_MIN_DATED:
        return None

    sub = df[dated]
    when = liked[dated]
    is_bs = (
        (sub["bittersweet"] >= BITTERSWEET_THRESHOLD)
        & (sub["brightness"] > df["brightness"].median())
    ).to_numpy()
    hours = when.dt.hour.to_numpy()
    weekdays = when.dt.dayofweek.to_numpy()  # 0 = понедельник

    def buckets(idx: np.ndarray, size: int) -> tuple[list[int], list[float | None]]:
        counts = [int((idx == b).sum()) for b in range(size)]
        share = [
            _r(is_bs[idx == b].mean()) if counts[b] >= CLOCK_MIN_PER_BUCKET else None
            for b in range(size)
        ]
        return counts, share

    hour_counts, hour_bittersweet = buckets(hours, 24)
    weekday_counts, weekday_bittersweet = buckets(weekdays, 7)

    # инсайт: циклическое окно 3ч с максимальной долей биттерсвита
    insight = None
    best_share = 0.0
    for h in range(24):
        window = [(h + d) % 24 for d in range(CLOCK_WINDOW_HOURS)]
        total = sum(hour_counts[w] for w in window)
        if total < CLOCK_WINDOW_MIN_TRACKS:
            continue
        bs = int(sum(is_bs[np.isin(hours, window)]))
        share = bs / total
        if share > best_share:
            best_share = share
            insight = (
                f"биттерсвит чаще всего ты лайкаешь"
                f" в {h:02d}—{(h + CLOCK_WINDOW_HOURS - 1) % 24:02d}"
            )

    return {
        "n_dated": int(dated.sum()),
        "hour_counts": hour_counts,
        "hour_bittersweet": hour_bittersweet,
        "weekday_counts": weekday_counts,
        "weekday_bittersweet": weekday_bittersweet,
        "peak_hour": int(np.argmax(hour_counts)),
        "insight": insight,
    }


def entropy(clusters: Iterable[int]) -> dict:
    """Энтропия Шеннона по долям кластеров (SPEC v0.11 §A8).

    H = -sum p_i * log2(p_i) (в битах); на k равных долях H = log2(k).
    effective_moods = 2^H — «эффективное число настроений» (до 0.1).
    Вердикт: H < 1.5 «монолитный вкус», H < 2.2 «сфокусированный»,
    иначе «многоликий».
    """
    counts = Counter(clusters)
    n = sum(counts.values())
    h = -sum((c / n) * math.log2(c / n) for c in counts.values() if c)
    if h < ENTROPY_MONOLITH_MAX:
        verdict = "монолитный вкус"
    elif h < ENTROPY_FOCUSED_MAX:
        verdict = "сфокусированный"
    else:
        verdict = "многоликий"
    return {
        "h_bits": _r(h, 2),
        "effective_moods": _r(2 ** h, 1),
        "verdict": verdict,
    }


# ------------------------------------------------------------------ сборка

def build_observatory(
    payload: dict, reference: dict[str, Sequence[float]] | None = None,
) -> dict:
    """Все панели обсерватории из сохранённого payload портрета (SPEC v0.11 §A).

    reference — значения признаков для перцентилей радара (роут собирает их
    из медиан сохранённых портретов или демо-набора); None -> self_pct = 50.
    ValueError, если points пусты или без features (роут превращает в 409).
    Сборка одноразовая: t-SNE на сотнях точек занимает секунды,
    длительности логируются.
    """
    t0 = perf_counter()
    df = points_frame(payload.get("points") or [])

    t_tsne = perf_counter()
    tsne = tsne_coords(df)
    log.info("обсерватория: t-SNE занял %.2f с (n=%d)", perf_counter() - t_tsne, len(df))

    observatory = {
        "n_tracks": int(len(df)),
        "key_wheel": key_wheel(df["meta"]),
        "ridgelines": ridgelines(df),
        "radar": radar(df, reference),
        "pca": pca_axes(df),
        "tsne": tsne,
        "outliers": outliers(df),
        "clock": clock(df),
        "entropy": entropy(df["cluster"].tolist()),
    }
    log.info("обсерватория собрана за %.2f с (n=%d)", perf_counter() - t0, len(df))
    return observatory

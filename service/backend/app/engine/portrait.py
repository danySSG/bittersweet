"""Построение «акустического портрета» из DataFrame признаков.

Самодостаточная адаптация корневых cluster.py / deep.py:
перцентильные оси energy/valence, KMeans-кластеры настроений с человеческими
метками, биттерсвит-категория. Выход — JSON-контракт под фронт (см. docs/SPEC.md).

SPEC v0.6 §A2: примеры кластеров и хайлайты несут preview_url из кэша признаков
(examples_rich — аддитивно к examples-строкам, фронт-типы не ломаются).
Это единственное место, где build_portrait читает кэш — best effort,
несколько точечных SELECT по ключу artist+title; сбой кэша даёт null.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from app.engine import cache

log = logging.getLogger(__name__)

# Фокусные «настроенческие» оси (подмножество из спеки, есть и в demo-CSV,
# и в выходе engine.features.analyze()).
MOOD_FEATURES = ["tempo", "minor_score", "bittersweet", "percussive",
                 "energy_rms", "brightness", "onset_rate"]

MIN_TRACKS = 12

# палитра кластеров (бренд-рампы)
PALETTE = ["#7F77DD", "#5DCAA5", "#D85A30", "#D4537E", "#378ADD", "#EF9F27", "#9F7BEA", "#63992D"]

BITTERSWEET_THRESHOLD = 0.86

# --- Страховка старого кэша от октавных ошибок темпа (SPEC v0.13 §A2) ---
# В кэше признаков живут дескрипторы, посчитанные ДО перцептивного выбора
# темпа (features.pick_perceptual_tempo, v0.13): у части треков tempo завышен
# октавой (Эйнауди: 235 при ощущаемых ~59). Переанализа аудио нет — фолдим
# на построении портрета по эвристике «событий заметно меньше, чем нот
# на долю»: настоящие tempo/60 долей в секунду дали бы сопоставимый
# onset_rate, а октавная ошибка — вдвое-вчетверо меньший.
FOLD_TEMPO_MIN = 165.0     # ниже — честные быстрые треки, не трогаем
# 0.55 -> 0.75 (пост-верификация v0.13): ловит фортепианные арпеджио-прелюдии
# (Rousseau/Бах: 258 bpm при 3.05 атак/сек — октавный уровень нотного потока),
# не задевая честный быстрый материал (162/5.0 и 172/9.8 — вне порога).
FOLD_ONSET_PER_BEAT = 0.75  # onset_rate < долей/сек * 0.75 => темп подозрителен


def fold_cached_tempo(tempo: float, onset_rate: float) -> float:
    """Фолд октавно-завышенного темпа из старого кэша (SPEC v0.13 §A2).

    Пока tempo >= 165 И onset_rate < tempo/60 * 0.55 (атак заметно меньше,
    чем «нот на долю») — tempo делится пополам. Честный быстрый материал
    не трогается: у него плотность атак сопоставима с долями.

    Примеры: (235, 2.1) -> 117.5 (117.5 < 165 — стоп);
    (162, 5.0) -> 162 (реальный быстрый дарквейв, порог не достигнут);
    (172, 9.8) -> 172 (атак много — темп честный).
    """
    try:
        t, onsets = float(tempo), float(onset_rate)
    except (TypeError, ValueError):
        return tempo
    while t >= FOLD_TEMPO_MIN and onsets < t / 60.0 * FOLD_ONSET_PER_BEAT:
        t /= 2.0
    return t

# --- timeline динамики вкуса по датам лайков (SPEC v0.10 §A) ---
TIMELINE_MIN_LIKED_SHARE = 0.6  # минимум точек с liked_at, чтобы строить timeline
TIMELINE_MIN_MONTHS = 3         # минимум разных календарных месяцев покрытия
TIMELINE_MIN_BUCKET_TRACKS = 8  # соседние бакеты мельче — сливаются


def _pct(s: pd.Series) -> pd.Series:
    """Перцентильный ранг 0..1."""
    return s.rank(pct=True)


def _choose_k(X: np.ndarray, lo: int = 3, hi: int = 6) -> int:
    """Число кластеров по силуэту (адаптация choose_k из cluster.py/deep.py)."""
    hi = min(hi, len(X) - 1)
    lo = min(lo, hi)
    if hi < 2:
        return 2
    lo = max(lo, 2)
    scores = {
        k: silhouette_score(X, KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(X))
        for k in range(lo, hi + 1)
    }
    return max(scores, key=scores.get)


# --- Архетипы кластеров (SPEC v0.3 §A, бренд-язык tastemap) ---
# Таблица данных «условие -> имя», НЕ if-лестница: правила настраивает продукт.
# Правило: (условие(means, med) -> bool, name, emoji), где means — средние
# признаков кластера, med — медианы всей библиотеки. Проверяются по порядку,
# первое совпавшее побеждает.
ArchetypeRule = tuple[Callable[[pd.Series, pd.Series], bool], str, str]

ARCHETYPE_RULES: list[ArchetypeRule] = [
    (lambda c, m: c["bittersweet"] >= 0.80,
     "биттерсвит", "🍬"),
    (lambda c, m: c["minor_score"] >= 0.60 and c["tempo"] > m["tempo"]
     and (c["percussive"] > m["percussive"] or c["onset_rate"] > m["onset_rate"]),
     "грустный бэнгер", "🌒"),
    (lambda c, m: c["minor_score"] >= 0.60 and c["percussive"] < m["percussive"]
     and c["energy_rms"] < m["energy_rms"],
     "тихая грусть", "🌫️"),
    (lambda c, m: c["minor_score"] <= 0.42 and c["brightness"] > m["brightness"],
     "светлая сторона", "☀️"),
    (lambda c, m: c["percussive"] > m["percussive"] and c["onset_rate"] > m["onset_rate"],
     "грув", "🕺"),
    (lambda c, m: c["brightness"] < m["brightness"],
     "тёмная материя", "🌑"),
]
ARCHETYPE_FALLBACK = ("между строк", "🌗")

# фиксированный словарь имён — измерения вектора-отпечатка в engine.compare
ARCHETYPE_NAMES = [name for _, name, _ in ARCHETYPE_RULES] + [ARCHETYPE_FALLBACK[0]]


def _archetype(cluster_means: pd.Series, lib_medians: pd.Series) -> dict:
    """Архетип кластера по таблице ARCHETYPE_RULES (первое совпавшее правило)."""
    for cond, name, emoji in ARCHETYPE_RULES:
        if cond(cluster_means, lib_medians):
            return {"name": name, "emoji": emoji}
    name, emoji = ARCHETYPE_FALLBACK
    return {"name": name, "emoji": emoji}


def _dedupe_name(name: str, seen: set[str], tempo_median: float) -> str:
    """Дубли имён (меток и архетипов) различаем темпом: «грустный бэнгер · ~162 bpm».

    Иначе и пользователь путается, и React ругается на дубли ключей.
    """
    if name in seen:
        name = f"{name} · ~{round(float(tempo_median))} bpm"
    while name in seen:  # крайний случай: совпал и темп
        name += " ·"
    seen.add(name)
    return name


def _mood_label(row: pd.Series, med: pd.Series) -> str:
    """Человеческая метка кластера относительно медиан библиотеки (из deep.py)."""
    if row["minor_score"] >= 0.60:
        mood = "меланхоличное"
    elif row["minor_score"] <= 0.42:
        mood = "светлое"
    elif row["bittersweet"] >= BITTERSWEET_THRESHOLD:
        mood = "биттерсвит"
    else:
        mood = "смешанное"
    e = (
        (row["tempo"] > med["tempo"])
        + (row["percussive"] > med["percussive"])
        + (row["onset_rate"] > med["onset_rate"])
    )
    energy = "энергичное" if e >= 2 else ("спокойное" if e == 0 else "среднее")
    timbre = "тёмное" if row["brightness"] < med["brightness"] else "яркое"
    return f"{energy} · {mood} · {timbre}"


def _track_label(row: pd.Series) -> str:
    return f"{row['artists']} — {row['title']}"


def _video_id(r: pd.Series) -> str | None:
    """Настоящий videoId строки или None (SPEC v0.4 §A1, v0.10 §D).

    Spotify-поток подставляет вместо videoId кэш-ключ («at:…»/«isrc:…»,
    см. pipeline.analyze_tracks) — такие не отдаём: ни радио, ни полный
    YouTube-плеер по ним не построить.
    """
    vid = r.get("videoId") if "videoId" in r.index else None
    if vid is None or pd.isna(vid):
        return None
    vid = str(vid)
    return None if ":" in vid else vid


def _liked_at_iso(r: pd.Series) -> str | None:
    """liked_at строки как ISO-строка или None (SPEC v0.10 §A)."""
    if "liked_at" not in r.index:
        return None
    val = r["liked_at"]
    try:
        if val is None or pd.isna(val):
            return None
    except (TypeError, ValueError):  # pd.isna на экзотике
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _preview_for(row: pd.Series) -> str | None:
    """preview_url трека из кэша признаков (SPEC v0.6 §A2) или None.

    Ключ — artist+title (ISRC в DataFrame не доезжает); старые кэш-записи
    без preview_url дают None — их добирает POST /api/preview/resolve.
    Best effort: проблемы кэша не должны ронять построение портрета.
    """
    try:
        key = cache.key_for(None, str(row["artists"]), str(row["title"]))
        return cache.get_preview_url(key)
    except Exception:
        log.exception("preview_url из кэша не прочитался: %s", row.get("title"))
        return None


def _highlights(df: pd.DataFrame, X: np.ndarray) -> list[dict]:
    """Хайлайты «самые-самые» (SPEC v0.3 §A): до 5, трек попадает только в один.

    Приоритет сверху вниз: занятый трек выбывает, следующий kind берёт
    лучший из оставшихся; kind без кандидатов пропускается.
    """
    n = len(df)
    dist = np.linalg.norm(X - X.mean(axis=0), axis=1)
    dmax = float(dist.max()) if float(dist.max()) > 0 else 1.0
    typicality = 100 * (1 - dist / dmax)  # 100 = центр вкуса библиотеки

    # (kind, title, score «больше = лучше», value(i) -> str, маска допуска)
    specs: list[tuple[str, str, np.ndarray, Callable[[int], str], np.ndarray]] = [
        ("fastest", "самый быстрый", df["tempo"].to_numpy(float),
         lambda i: f"{round(float(df['tempo'].iloc[i]))} bpm", np.ones(n, bool)),
        ("darkest", "самый тёмный", -df["brightness"].to_numpy(float),
         lambda i: f"{round(float(df['brightness'].iloc[i]))} Гц", np.ones(n, bool)),
        ("most_bittersweet", "самый биттерсвит", df["bittersweet"].to_numpy(float),
         lambda i: f"{float(df['bittersweet'].iloc[i]):.2f}",
         df["bittersweet"].to_numpy(float) >= 0.8),
        ("most_energetic", "самый заряженный", df["energy_rms"].to_numpy(float),
         lambda i: f"{float(df['energy_rms'].iloc[i]):.2f}", np.ones(n, bool)),
        ("most_yours", "самый типичный", typicality,
         lambda i: f"{round(float(typicality[i]))}% совпадение", np.ones(n, bool)),
    ]

    used: set[int] = set()
    out: list[dict] = []
    for kind, title, score, value, ok in specs:
        mask = ok.copy()
        if used:
            mask[list(used)] = False
        if not mask.any():
            continue
        candidates = np.flatnonzero(mask)
        i = int(candidates[np.argmax(score[candidates])])
        used.add(i)
        out.append({"kind": kind, "title": title,
                    "track": _track_label(df.iloc[i]), "value": value(i),
                    # SPEC v0.6 §A2: «самые-самые» проигрываются; может быть null
                    "preview_url": _preview_for(df.iloc[i]),
                    # SPEC v0.10 §D: полный YouTube-плеер; может быть null
                    "videoId": _video_id(df.iloc[i])})
    return out


def _parse_liked_at(df: pd.DataFrame) -> pd.Series:
    """Колонка liked_at -> tz-aware datetime-Series (мусор/пропуски -> NaT).

    format="ISO8601": все источники отдают ISO (publishedAt YouTube,
    isoformat из Takeout, валидированная метка ytmusicapi) — парсим строго
    и без «угадывания формата по элементам».
    """
    if "liked_at" not in df.columns:
        return pd.Series(pd.NaT, index=df.index)
    return pd.to_datetime(df["liked_at"], errors="coerce", utc=True, format="ISO8601")


def _build_timeline(df: pd.DataFrame, med: pd.Series) -> list[dict] | None:
    """Timeline динамики вкуса по датам лайков (SPEC v0.10 §A) или None.

    None — НЕ ошибка, а честное «динамику показать не из чего»:
    - liked_at меньше чем у TIMELINE_MIN_LIKED_SHARE (60%) точек, ИЛИ
    - покрытие меньше TIMELINE_MIN_MONTHS (3) разных календарных месяцев, ИЛИ
    - после слияния остался один бакет (все лайки — фактически один период).

    Алгоритм слияния бакетов — адаптивный и детерминированный:
    1. Начальные бакеты — НЕПУСТЫЕ календарные месяцы, хронологически.
    2. Пока бакетов больше одного и самый маленький < TIMELINE_MIN_BUCKET_TRACKS
       (8) треков: берём самый маленький бакет (при равенстве размеров —
       самый ранний) и сливаем его с МЕНЬШИМ из соседей (при равенстве —
       с левым/ранним; у крайних бакетов сосед один). Слитый бакет занимает
       объединённый период от начала левого до конца правого.
    3. Так месяцы укрупняются до кварталов/полугодий ровно там, где лайков
       мало, а плотные периоды остаются месячными. Никакой случайности —
       результат зависит только от данных (детерминированность).

    На бакет: {period_start, period_end (ISO-даты, календарные границы
    месяцев), n_tracks, tempo_median, minor_share, bittersweet_share (обе
    в процентах; биттерсвит — по портретному определению: bittersweet >=
    BITTERSWEET_THRESHOLD и brightness выше медианы библиотеки),
    top_archetype{name, emoji} (по ARCHETYPE_RULES на средних бакета
    против медиан всей библиотеки)}. Бакеты — хронологически.
    """
    liked = _parse_liked_at(df)
    n = len(df)
    dated = liked.dropna()
    if n == 0 or len(dated) < TIMELINE_MIN_LIKED_SHARE * n:
        return None
    # tz снимаем (всё уже в UTC): to_period на tz-aware ругается предупреждением
    months = dated.dt.tz_localize(None).dt.to_period("M")
    if months.nunique() < TIMELINE_MIN_MONTHS:
        return None

    # 1. начальные бакеты: непустые календарные месяцы, хронологически
    by_month: dict[pd.Period, list] = {}
    for idx, period in months.items():
        by_month.setdefault(period, []).append(idx)
    buckets = [
        {"start": p, "end": p, "idx": ids}
        for p, ids in sorted(by_month.items(), key=lambda kv: kv[0])
    ]

    # 2. адаптивное слияние (см. докстринг): мелкий бакет + меньший сосед
    while len(buckets) > 1:
        sizes = [len(b["idx"]) for b in buckets]
        if min(sizes) >= TIMELINE_MIN_BUCKET_TRACKS:
            break
        i = sizes.index(min(sizes))  # при равенстве — самый ранний
        if i == 0:
            j = 1
        elif i == len(buckets) - 1:
            j = i - 1
        else:
            j = i - 1 if sizes[i - 1] <= sizes[i + 1] else i + 1
        lo, hi = min(i, j), max(i, j)
        buckets[lo : hi + 1] = [{
            "start": buckets[lo]["start"],
            "end": buckets[hi]["end"],
            "idx": buckets[lo]["idx"] + buckets[hi]["idx"],
        }]

    # 3. один бакет — динамики нет (см. докстринг)
    if len(buckets) < 2:
        return None

    out: list[dict] = []
    for b in buckets:
        sub = df.loc[b["idx"]]
        minor_share = (
            float((sub["mode"] == "minor").mean() * 100) if "mode" in sub.columns
            else float((sub["minor_score"] >= 0.5).mean() * 100)
        )
        bittersweet_share = float((
            (sub["bittersweet"] >= BITTERSWEET_THRESHOLD)
            & (sub["brightness"] > med["brightness"])
        ).mean() * 100)
        out.append({
            "period_start": b["start"].start_time.date().isoformat(),
            "period_end": b["end"].end_time.date().isoformat(),
            "n_tracks": int(len(sub)),
            "tempo_median": round(float(sub["tempo"].median())),
            "minor_share": round(minor_share),
            "bittersweet_share": round(bittersweet_share),
            "top_archetype": _archetype(sub[MOOD_FEATURES].mean(), med),
        })
    return out


def build_portrait(df: pd.DataFrame, k: int | None = None) -> dict:
    """DataFrame признаков -> JSON-портрет по контракту из SPEC.md.

    Ожидает колонки MOOD_FEATURES + artists, title (+ опционально videoId,
    key, mode, status, liked_at). Строки со status != 'ok' отбрасываются.
    liked_at (SPEC v0.10 §A) уходит в points[].liked_at (ISO, nullable)
    и питает timeline (см. _build_timeline; нет данных -> timeline: null).
    """
    df = df.copy()
    if "status" in df.columns:
        df = df[df["status"] == "ok"]
    if "videoId" in df.columns:
        df = df.drop_duplicates("videoId")
    for col in MOOD_FEATURES:
        if col not in df.columns:
            raise ValueError(f"нет колонки признака: {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=MOOD_FEATURES).reset_index(drop=True)

    # SPEC v0.13 §A2: страховка старого кэша — октавно-завышенный tempo
    # фолдится ДО всех расчётов: хайлайт «самый быстрый», медианы, архетипы,
    # timeline и meta точек автоматически честнее при перестройке
    if len(df):
        df["tempo"] = [
            fold_cached_tempo(t, o)
            for t, o in zip(df["tempo"].to_numpy(float), df["onset_rate"].to_numpy(float))
        ]

    n = len(df)
    if n < MIN_TRACKS:
        raise ValueError(f"слишком мало треков для портрета: {n} (нужно >= {MIN_TRACKS})")

    # --- перцентильные оси карты (адаптация cluster.py) ---
    df["energy"] = 100 * (
        _pct(df["tempo"]) + _pct(df["energy_rms"]) + _pct(df["percussive"]) + _pct(df["onset_rate"])
    ) / 4
    df["valence"] = 100 * (_pct(1 - df["minor_score"]) + _pct(df["brightness"])) / 2

    # --- кластеры настроений ---
    X = StandardScaler().fit_transform(df[MOOD_FEATURES].values)
    if k is None:
        k = _choose_k(X)
    k = max(2, min(int(k), n - 1))
    labels = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(X)
    df["cluster"] = labels

    med = df[MOOD_FEATURES].median()
    order = sorted(range(k), key=lambda c: -int((labels == c).sum()))  # по размеру
    cluster_index = {c: i for i, c in enumerate(order)}

    clusters = []
    seen_labels: set[str] = set()
    seen_archetypes: set[str] = set()
    for c in order:
        idx = np.where(labels == c)[0]
        sub = df.iloc[idx]
        centroid = X[idx].mean(axis=0)
        nearest = sub.iloc[np.argsort(np.linalg.norm(X[idx] - centroid, axis=1))[:4]]
        minor_share = (
            float((sub["mode"] == "minor").mean() * 100) if "mode" in sub.columns
            else float((sub["minor_score"] >= 0.5).mean() * 100)
        )
        means = sub[MOOD_FEATURES].mean()
        tempo_median = float(sub["tempo"].median())
        # человеческие метки и архетипы могут совпасть у разных кластеров — дедуп темпом
        label = _dedupe_name(_mood_label(means, med), seen_labels, tempo_median)
        archetype = _archetype(means, med)
        archetype["name"] = _dedupe_name(archetype["name"], seen_archetypes, tempo_median)
        clusters.append({
            "label": label,
            "archetype": archetype,
            "size": int(len(idx)),
            "share": round(len(idx) / n * 100),
            "medians": {
                "tempo": round(tempo_median),
                "minor_share": round(minor_share),
                "brightness": round(float(sub["brightness"].median())),
            },
            "examples": [_track_label(r) for _, r in nearest.iterrows()],
            # SPEC v0.6 §A2: аддитивно к examples (строки остаются для старых
            # фронт-типов) — те же треки с preview_url из кэша (может быть null).
            # SPEC v0.10 §D: + videoId (может быть null) — полный YouTube-плеер.
            "examples_rich": [
                {"label": _track_label(r), "preview_url": _preview_for(r),
                 "videoId": _video_id(r)}
                for _, r in nearest.iterrows()
            ],
            "color": PALETTE[cluster_index[c] % len(PALETTE)],
        })

    # --- биттерсвит: на грани мажор/минор и при этом светлый тембр (deep.py) ---
    bs = df[
        (df["bittersweet"] >= BITTERSWEET_THRESHOLD) & (df["brightness"] > med["brightness"])
    ].sort_values("bittersweet", ascending=False)
    bittersweet = {
        "count": int(len(bs)),
        "share": round(len(bs) / n * 100),
        "top": [_track_label(r) for _, r in bs.head(8).iterrows()],
        # доля сохранённых портретов с меньшим share; проставляется «на лету»
        # слоем хранения (engine.store.attach_bittersweet_percentile), не здесь —
        # build_portrait не ходит в базу
        "percentile": None,
    }

    # --- точки карты: x = valence-перцентиль, y = energy-перцентиль (без UMAP в MVP) ---
    def _meta(r: pd.Series) -> str:
        if "key" in r.index and "mode" in r.index and pd.notna(r.get("key")):
            return f"{r['tempo']:.0f}bpm · {r['key']} {r['mode']}"
        return f"{r['tempo']:.0f}bpm"

    points = [
        {
            "x": round(float(r["valence"]), 1),
            "y": round(float(r["energy"]), 1),
            "cluster": cluster_index[int(r["cluster"])],
            "label": _track_label(r),
            "meta": _meta(r),
            # videoId + сырые MOOD_FEATURES — топливо discovery (SPEC v0.4):
            # сиды и центроид кластера восстанавливаются из точек без пересчёта.
            # Контракт-аддитивно, фронт эти поля игнорирует.
            "videoId": _video_id(r),
            # дата лайка (ISO, nullable) — динамика вкуса (SPEC v0.10 §A)
            "liked_at": _liked_at_iso(r),
            "features": {f: round(float(r[f]), 4) for f in MOOD_FEATURES},
        }
        for _, r in df.iterrows()
    ]

    fingerprint = {
        "tempo_median": round(float(df["tempo"].median())),
        "minor_share": round(
            float((df["mode"] == "minor").mean() * 100) if "mode" in df.columns
            else float((df["minor_score"] >= 0.5).mean() * 100)
        ),
        "brightness_mean": round(float(df["brightness"].mean())),
    }

    return {
        "n_tracks": n,
        "clusters": clusters,
        "bittersweet": bittersweet,
        "highlights": _highlights(df, X),
        "points": points,
        "fingerprint": fingerprint,
        # SPEC v0.10 §A (аддитивно): динамика вкуса по датам лайков;
        # null — liked_at мало или покрытие < 3 месяцев (это не ошибка)
        "timeline": _build_timeline(df, med),
    }

"""«История» — портрет, рассказанный словами (SPEC v0.12 §A).

build_story(payload, portrait_id) собирает структурированную историю из
СОХРАНЁННОГО payload портрета (portrait + observatory + timeline):
{chapters: [{id, emoji, title, paragraphs, interactive|null}]}. Главы без
данных честно пропускаются — это не ошибка, а «рассказать нечего».

Тон — тёплый, на «ты», с лёгкой иронией; технический жаргон в
пользовательском тексте запрещён (перцентиль, кластер, PCA, энтропия,
KDE, стандартизация — вместо них «настроение», «личности», «странности»).

Вариативность: на каждую подстановку 2-3 формулировки; выбор детерминирован
от portrait_id (crc32 от id+соли слота, никакого random) — у одного портрета
история всегда одна и та же, у разных портретов — разные интонации.

Сборка ленивая и одноразовая (как observatory): роут кладёт результат в
payload (store.save_story), повторные GET отдают из payload без пересчёта.
"""

from __future__ import annotations

import logging
import re
import zlib
from collections.abc import Callable, Sequence
from datetime import date

from app.engine import cache

log = logging.getLogger(__name__)

# версия схемы story в payload: несовпадение = пересчитать
STORY_VERSION = 3  # v3: честное покрытие в главе identity (SPEC v0.13 §B)

# порог покрытия: ниже — identity честно признаётся, что часть треков
# не поймалась (нет 30-сек превью в открытых каталогах)
COVERAGE_PHRASE_BELOW = 0.85

# минимальный скачок (п.п.) minor/bittersweet между соседними бакетами
# timeline, чтобы считать его «переломом» (глава story_of_time)
MIN_TURN_DELTA = 5

# сколько переломов рассказываем (самые резкие |дельты|)
MAX_TURNS = 3

# ноты по-русски — «музыкальный дом» (глава home_key)
NOTE_RU = {
    "C": "до", "C#": "до-диез", "D": "ре", "D#": "ре-диез", "E": "ми",
    "F": "фа", "F#": "фа-диез", "G": "соль", "G#": "соль-диез",
    "A": "ля", "A#": "ля-диез", "B": "си",
}

# месяцы в предложном падеже («в марте 2024-го»)
MONTHS_PREP = [
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
]

# доля 0..1 -> человеческая фраза; полуинтервалы [lo, следующий lo)
# сверху вниз, фразы самодостаточны («каждый третий трек», не «33%»)
_SHARE_BANDS: list[tuple[float, str]] = [
    (0.93, "почти каждый трек"),
    (0.70, "три трека из четырёх"),
    (0.60, "два трека из трёх"),
    (0.55, "чуть больше половины треков"),
    (0.45, "примерно половина треков"),
    (0.37, "почти половина треков"),
    (0.28, "каждый третий трек"),
    (0.22, "каждый четвёртый трек"),
    (0.15, "каждый пятый трек"),
    (0.08, "примерно каждый десятый трек"),
    (0.03, "несколько треков из ста"),
    (0.0, "считанные треки"),
]

Pick = Callable[[str, Sequence[str]], str]


def share_to_phrase(share: float) -> str:
    """Доля 0..1 -> человеческая фраза («каждый третий трек»).

    Вход клампится в [0, 1] (проценты сюда не передавать — делить на 100).
    Полосы — полуинтервалы: границы принадлежат верхней полосе
    (0.45 — уже «примерно половина треков»).
    """
    s = min(1.0, max(0.0, float(share)))
    for lo, phrase in _SHARE_BANDS:
        if s >= lo:
            return phrase
    return _SHARE_BANDS[-1][1]


def _picker(portrait_id: str) -> Pick:
    """Детерминированный выбор формулировки: crc32(portrait_id + соль слота).

    Никакого random: одна и та же история у портрета при каждом пересчёте,
    но разные портреты получают разные интонации.
    """
    def pick(salt: str, variants: Sequence[str]) -> str:
        h = zlib.crc32(f"{portrait_id}:{salt}".encode())
        return variants[h % len(variants)]
    return pick


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование числительных: 1 трек / 2 трека / 5 треков."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _preview_for_label(label: str | None) -> str | None:
    """preview_url по label «Артист — Название» из кэша признаков (best effort)."""
    if not label or " — " not in label:
        return None
    artist, title = label.split(" — ", 1)
    try:
        return cache.get_preview_url(cache.key_for(None, artist, title))
    except Exception:
        return None


def _key_ru(key: str, mode: str) -> str:
    """«ля-минор» из ("A", "minor"); незнакомая нота — как есть."""
    note = NOTE_RU.get(key, key)
    return f"{note}-{'минор' if mode == 'minor' else 'мажор'}"


def _period_ru(start_iso: str, end_iso: str) -> str:
    """Период бакета timeline в предложном падеже: «марте 2024-го»,
    «марте–июне 2024-го», «ноябре 2023-го — феврале 2024-го»."""
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    if (start.year, start.month) == (end.year, end.month):
        return f"{MONTHS_PREP[start.month - 1]} {start.year}-го"
    if start.year == end.year:
        return f"{MONTHS_PREP[start.month - 1]}–{MONTHS_PREP[end.month - 1]} {start.year}-го"
    return (
        f"{MONTHS_PREP[start.month - 1]} {start.year}-го — "
        f"{MONTHS_PREP[end.month - 1]} {end.year}-го"
    )


def _coverage_sentence(payload: dict, pick: Pick) -> str | None:
    """Предложение о покрытии (SPEC v0.13 §B) или None.

    Появляется только при покрытии < COVERAGE_PHRASE_BELOW (85%):
    payload["coverage"] = {candidates, analyzed} кладут джобы анализа.
    Старые портреты без coverage — молча None (не ошибка).
    """
    cov = payload.get("coverage") or {}
    try:
        candidates = int(cov.get("candidates"))
        analyzed = int(cov.get("analyzed"))
    except (TypeError, ValueError):
        return None
    if candidates <= 0 or analyzed <= 0 or analyzed >= candidates:
        return None
    if analyzed / candidates >= COVERAGE_PHRASE_BELOW:
        return None
    return pick("identity.coverage", [
        f"Поймать удалось {analyzed} из {candidates} твоих треков — самые "
        "редкие спрятались от каталогов превью; портрет честен, но чуть "
        "смещён к известному.",
        f"В кадр попали {analyzed} из {candidates} треков: у самых редких "
        "не нашлось превью в открытых каталогах, так что портрет честен, "
        "но чуть смещён к известному.",
    ])


# ------------------------------------------------------------------- главы

def _identity(payload: dict, obs: dict, pick: Pick) -> dict | None:
    """«Кто ты»: топ-архетип + доля + «сколько в тебе личностей»."""
    clusters = payload.get("clusters") or []
    if not clusters:
        return None
    top = clusters[0]
    arche = top.get("archetype") or {}
    name = arche.get("name") or top.get("label") or "твоё главное настроение"
    emoji = arche.get("emoji") or "🎭"
    phrase = share_to_phrase(float(top.get("share") or 0) / 100)

    ent = obs.get("entropy") or {}
    moods = ent.get("effective_moods")
    n = max(1, round(float(moods))) if isinstance(moods, (int, float)) else len(clusters)
    if n == 1:
        opening = pick("identity.open1", [
            "Внутри тебя живёт одна музыкальная личность — зато какая.",
            "У тебя ровно одна музыкальная личность, и она точно знает, чего хочет.",
        ])
    else:
        pers = f"{n} {_plural(n, 'музыкальная личность', 'музыкальные личности', 'музыкальных личностей')}"
        opening = pick("identity.open", [
            f"В тебе уживаются примерно {pers}, и они на удивление неплохо ладят.",
            f"Если разложить твой вкус по полочкам, получится примерно {pers}.",
            f"Твой вкус — это примерно {pers}, и у каждой свой плейлист.",
        ])
    main = pick("identity.main", [
        f"Главная в этой компании — {emoji} «{name}»: {phrase}.",
        f"Но чаще всех к колонкам прорывается {emoji} «{name}» — {phrase}.",
        f"Солирует {emoji} «{name}»: {phrase}.",
    ])
    paragraphs = [f"{opening} {main}"]

    verdict_variants = {
        "монолитный вкус": [
            "И вообще у тебя на удивление цельный вкус: почти всё, что ты слушаешь, — из одной вселенной.",
            "Вкус у тебя цельный, без хаоса: одна ясная линия.",
        ],
        "сфокусированный": [
            "Вкус у тебя собранный: несколько любимых настроений — и ты их держишься.",
            "Ты не разбрасываешься: пара-тройка настроений покрывает почти всё.",
        ],
        "многоликий": [
            "Вкус у тебя пёстрый: за один вечер ты успеваешь съездить в несколько музыкальных стран.",
            "Настроений у тебя много, и все живые — скучных вечеров не бывает.",
        ],
    }.get(ent.get("verdict"))
    if verdict_variants:
        paragraphs.append(pick("identity.verdict", verdict_variants))

    # SPEC v0.13 §B: честное покрытие — одно предложение при < 85%
    coverage = _coverage_sentence(payload, pick)
    if coverage:
        paragraphs.append(coverage)

    return {
        "id": "identity",
        "emoji": emoji,
        "title": pick("identity.title", ["Кто ты", "Кто тут главный", "Главная роль"]),
        "paragraphs": paragraphs,
        "interactive": {
            "kind": "archetype_chips",
            "data": {"archetypes": [
                {
                    "cluster": i,
                    "name": (c.get("archetype") or {}).get("name") or c.get("label"),
                    "emoji": (c.get("archetype") or {}).get("emoji") or "🎵",
                    "share": c.get("share"),
                    "color": c.get("color"),
                }
                for i, c in enumerate(clusters)
            ]},
        },
    }


def _tempo_sentence(tempo: int, pick: Pick) -> str:
    """Темп через якорь-пульс: «~118 ударов в минуту — как сердце на прогулке»."""
    if tempo < 85:
        anchors = [
            "медленнее спокойного сердцебиения; под такое выдыхают",
            "как пульс во сне — твоя музыка никуда не торопится",
        ]
    elif tempo < 105:
        anchors = [
            "как пульс человека, которому некуда спешить",
            "спокойный шаг, а не бег",
        ]
    elif tempo < 125:
        anchors = [
            "как сердце на быстрой прогулке",
            "как пульс человека, который бодро шагает по своим делам",
        ]
    elif tempo < 145:
        anchors = [
            "как сердце на лёгкой пробежке",
            "уже почти бег — музыка тебя подгоняет",
        ]
    else:
        anchors = [
            "как сердце после спринта",
            "это уже не прогулка, а погоня",
        ]
    opening = pick("sound.tempo_open", [
        "Средний пульс твоей музыки", "Пульс твоей библиотеки",
    ])
    beats = _plural(tempo, "удар", "удара", "ударов")
    return f"{opening} — ~{tempo} {beats} в минуту: {pick('sound.tempo', anchors)}."


def _brightness_sentence(brightness: float, pick: Pick) -> str:
    """Яркость тембра через свет: лампа / дневной свет / утро без штор."""
    if brightness < 1400:
        variants = [
            "Тембр — тёплый и приглушённый: лампа, а не люминесцентная контора.",
            "Звук у тебя тёплый и приглушённый — торшер вечером, а не офисный свет.",
        ]
    elif brightness < 2400:
        variants = [
            "Тембр — ровный и мягкий, как дневной свет из окна.",
            "По тембру ты посередине: не бархат и не звон, а спокойный дневной свет.",
        ]
    else:
        variants = [
            "Тембр — яркий и звонкий, как утро без штор.",
            "Звук у тебя яркий, с верхних этажей: звенит и искрится.",
        ]
    return pick("sound.brightness", variants)


def _minor_sentence(minor_share: float, pick: Pick) -> str:
    """Минор/мажор; фразы через тире — согласуются с любым числом."""
    phrase = share_to_phrase(minor_share / 100)
    if minor_share >= 80:
        variants = [
            f"Минор здесь хозяин: {phrase} — в нём.",
            f"Тёмная сторона победила: {phrase} у тебя — в миноре.",
        ]
    elif minor_share >= 60:
        variants = [
            f"Минор у тебя дома: {phrase} — в нём.",
            f"Ты явно за тёмную сторону: {phrase} — в миноре.",
        ]
    elif minor_share >= 40:
        variants = [
            "Минор и мажор делят твою библиотеку примерно пополам — и, похоже, не ссорятся.",
            "Ты на границе света и тени: минор и мажор идут почти вровень.",
        ]
    else:
        variants = [
            f"Ты из команды света: минор здесь в гостях — {phrase}.",
            f"Мажор уверенно ведёт: минора немного — {phrase}.",
        ]
    return pick("sound.minor", variants)


def _sound(payload: dict, obs: dict, pick: Pick) -> dict | None:
    """«Как звучит твой вкус»: темп/яркость/минор через сравнения-якоря."""
    fp = payload.get("fingerprint") or {}
    tempo, brightness = fp.get("tempo_median"), fp.get("brightness_mean")
    if tempo is None or brightness is None:
        return None
    paragraphs = [
        f"{_tempo_sentence(round(float(tempo)), pick)} "
        f"{_brightness_sentence(float(brightness), pick)}"
    ]
    if fp.get("minor_share") is not None:
        paragraphs.append(_minor_sentence(float(fp["minor_share"]), pick))

    interactive = None
    yours = next(
        (h for h in payload.get("highlights") or [] if h.get("kind") == "most_yours"),
        None,
    )
    if yours and yours.get("track") and (yours.get("preview_url") or yours.get("videoId")):
        track = yours["track"]
        paragraphs.append(pick("sound.yours", [
            f"А если сжать всё это в один трек, получится «{track}» — послушай, это буквально ты.",
            f"Хочешь услышать всё это разом? Вот трек, который звучит как ты весь сразу: «{track}».",
        ]))
        interactive = {
            "kind": "play_track",
            "data": {
                "label": track,
                "videoId": yours.get("videoId"),
                "preview_url": yours.get("preview_url"),
            },
        }
    return {
        "id": "sound",
        "emoji": "🎧",
        "title": pick("sound.title", ["Как звучит твой вкус", "Как ты звучишь"]),
        "paragraphs": paragraphs,
        "interactive": interactive,
    }


def _home_key(payload: dict, obs: dict, pick: Pick) -> dict | None:
    """«Твой музыкальный дом»: топ-тональность колеса + 2 примера с ▶."""
    top = (obs.get("key_wheel") or {}).get("top")
    if not top:
        return None
    key_ru = _key_ru(top.get("key", "?"), top.get("mode", "minor"))
    count = int(top.get("count") or 0)
    tracks_word = _plural(count, "трек", "трека", "треков")

    p1 = pick("home.p1", [
        f"Если у твоего вкуса есть дом, то это {key_ru}: сюда твоя музыка возвращается чаще всего.",
        f"Самая частая тональность у тебя — {key_ru}. Похоже, в ней тебе теплее всего.",
        f"У твоей библиотеки есть домашний адрес: {key_ru}.",
    ])
    if count:
        p1 += " " + pick("home.count", [
            f"Здесь — {count} {tracks_word}: больше, чем в любой другой тональности.",
            f"{count} {tracks_word} — рекорд среди всех тональностей.",
        ])

    # примеры из библиотеки в этой тональности: тональность лежит в meta
    # точки («144bpm · B minor»); сначала те, у которых есть videoId
    key_re = re.compile(
        rf"·\s*{re.escape(str(top.get('key')))}\s+{re.escape(str(top.get('mode')))}\s*$"
    )
    in_key = [
        p for p in payload.get("points") or []
        if p.get("label") and key_re.search(str(p.get("meta") or ""))
    ]
    examples = ([p for p in in_key if p.get("videoId")]
                + [p for p in in_key if not p.get("videoId")])[:2]

    paragraphs = [p1]
    interactive = None
    if examples:
        names = [f"«{p['label']}»" for p in examples]
        if len(names) == 2:
            paragraphs.append(pick("home.examples", [
                f"Например, {names[0]} и {names[1]} — оба оттуда.",
                f"Послушай {names[0]} или {names[1]} — вот так звучит твой дом.",
            ]))
        else:
            paragraphs.append(f"Например, {names[0]} — он как раз оттуда.")
        interactive = {
            "kind": "play_list",
            "data": {"items": [
                {
                    "label": p["label"],
                    "videoId": p.get("videoId"),
                    "preview_url": _preview_for_label(p["label"]),
                }
                for p in examples
            ]},
        }
    return {
        "id": "home_key",
        "emoji": "🏠",
        "title": pick("home.title", ["Твой музыкальный дом", "Где живёт твоя музыка"]),
        "paragraphs": paragraphs,
        "interactive": interactive,
    }


import re as _re

# Гц ламеру ничего не говорят (фидбек-редактура v0.12): переводим тембровые
# why-объяснения ворон в сравнения-якоря; bpm оставляем — якорь «как пульс»
# уже задан главой sound.
_WHY_HUMANIZE: list[tuple[_re.Pattern[str], str]] = [
    (_re.compile(r"экстремально тёмный \(\d+ Гц\)"),
     "звучит темнее всего в твоей коллекции — глухо, как из-под одеяла"),
    (_re.compile(r"экстремально яркий \(\d+ Гц\)"),
     "звенит ярче всего у тебя — как солнечный зайчик"),
    (_re.compile(r" \(\d+(?:\.\d+)? Гц\)"), ""),  # прочие герцы просто убираем
]


def _humanize_why(text: str) -> str:
    for pat, repl in _WHY_HUMANIZE:
        text = pat.sub(repl, text)
    return text


def _rare_birds(payload: dict, obs: dict, pick: Pick) -> dict | None:
    """«Твои странности»: верхние белые вороны с человеческим why — с любовью."""
    birds = (obs.get("outliers") or [])[:MAX_TURNS]
    if not birds:
        return None
    top = birds[0]
    why = [_humanize_why(w) for w in (top.get("why") or [])]
    if len(why) >= 2:
        why_txt = f"{why[0]}, да ещё и {why[1]}"
    elif why:
        why_txt = why[0]
    else:
        why_txt = "он вообще ни на что у тебя не похож"

    paragraphs = [pick("birds.top", [
        f"Самый неожиданный гость в твоей коллекции — «{top.get('label')}»: {why_txt}. Как он вообще к тебе попал?",
        f"Есть у тебя одна белая ворона — «{top.get('label')}»: {why_txt}. И ведь чем-то зацепила.",
    ])]
    rest = [b for b in birds[1:] if b.get("label")]
    if rest:
        names = " и ".join(f"«{b['label']}»" for b in rest)
        paragraphs.append(pick("birds.rest", [
            f"Компанию ему составляют {names} — тоже не из местных.",
            f"Рядом на жёрдочке — {names}: тоже прилетели откуда-то издалека.",
        ]))
    paragraphs.append(pick("birds.love", [
        "Не вздумай их удалять: такие странности — самая честная подпись вкуса.",
        "Это не ошибки, а особые приметы твоего вкуса. Береги их.",
    ]))
    return {
        "id": "rare_birds",
        "emoji": "🐦‍⬛",
        "title": pick("birds.title", ["Твои странности", "Твои белые вороны", "Странные гости"]),
        "paragraphs": paragraphs,
        "interactive": {
            "kind": "play_list",
            "data": {"items": [
                {
                    "label": b.get("label"),
                    "videoId": b.get("videoId"),
                    "preview_url": _preview_for_label(b.get("label")),
                }
                for b in birds
            ]},
        },
    }


def _story_of_time(payload: dict, obs: dict, pick: Pick) -> dict | None:
    """«Как менялся твой вкус»: самые резкие переломы minor/bittersweet."""
    timeline = payload.get("timeline")
    if not isinstance(timeline, list) or len(timeline) < 2:
        return None

    turns: list[tuple[float, int, str, int, int]] = []  # (|дельта|, i, метрика, с, до)
    for i in range(1, len(timeline)):
        prev, cur = timeline[i - 1], timeline[i]
        best: tuple[float, str, int, int] | None = None
        for metric in ("minor_share", "bittersweet_share"):
            a, b = prev.get(metric), cur.get(metric)
            if a is None or b is None:
                continue
            delta = abs(float(b) - float(a))
            if best is None or delta > best[0]:
                best = (delta, metric, round(float(a)), round(float(b)))
        if best and best[0] >= MIN_TURN_DELTA:
            turns.append((best[0], i, best[1], best[2], best[3]))
    if not turns:
        return None
    # самые резкие — но рассказываем хронологически
    chosen = sorted(sorted(turns, key=lambda t: -t[0])[:MAX_TURNS], key=lambda t: t[1])

    sentences = []
    for _, i, metric, a, b in chosen:
        cur = timeline[i]
        period = _period_ru(cur.get("period_start"), cur.get("period_end"))
        up = b > a
        if metric == "minor_share":
            variants = [
                f"В {period} тебя качнуло в тёмное: минор вырос с {a}% до {b}%.",
                f"В {period} наступила тёмная полоса — минор поднялся с {a}% до {b}%.",
            ] if up else [
                f"В {period} музыку повело к свету: минор упал с {a}% до {b}%.",
                f"В {period} стало заметно светлее — минор сполз с {a}% до {b}%.",
            ]
        else:
            variants = [
                f"В {period} тебя накрыло биттерсвитом: с {a}% до {b}%.",
                f"В {period} биттерсвит пошёл в рост: с {a}% до {b}%.",
            ] if up else [
                f"В {period} биттерсвит отпустил: с {a}% до {b}%.",
                f"В {period} биттерсвит стих — с {a}% до {b}%.",
            ]
        sentences.append(pick(f"time.turn{i}", variants))

    intro = pick("time.intro", [
        "Твой вкус не стоял на месте — вот самые резкие повороты.",
        "У этой истории есть сюжетные повороты.",
    ])
    return {
        "id": "story_of_time",
        "emoji": "🌊",
        "title": pick("time.title", ["Как менялся твой вкус", "Повороты сюжета"]),
        "paragraphs": [intro, " ".join(sentences)],
        "interactive": {"kind": "timeline_mini", "data": None},
    }


def _clock_chapter(payload: dict, obs: dict, pick: Pick) -> dict | None:
    """«Твои музыкальные часы»: пик лайков + инсайт биттерсвит-окна."""
    c = obs.get("clock")
    if not c or not isinstance(c.get("peak_hour"), int):
        return None
    h = c["peak_hour"]
    if h <= 4:
        daypart = "глубокой ночью"
    elif h <= 8:
        daypart = "ранним утром"
    elif h <= 11:
        daypart = "утром"
    elif h <= 16:
        daypart = "днём"
    elif h <= 21:
        daypart = "вечером"
    else:
        daypart = "ближе к ночи"
    paragraphs = [pick("clock.peak", [
        f"Чаще всего лайки у тебя случаются {daypart}: пик — около {h:02d}:00.",
        f"Твой звёздный час — {h:02d}:00. Именно {daypart} рука сама тянется к сердечку.",
    ])]
    if c.get("insight"):
        paragraphs.append(pick("clock.insight", [
            f"А ещё {c['insight']}.",
            f"Заметка на полях: {c['insight']}.",
        ]))
    return {
        "id": "clock",
        "emoji": "🌙",
        "title": pick("clock.title", ["Твои музыкальные часы", "Когда тебя накрывает"]),
        "paragraphs": paragraphs,
        "interactive": None,
    }


def _finale(payload: dict, obs: dict, pick: Pick) -> dict | None:
    """«Что дальше»: CTA-глава — радио / копнуть карту / квиз."""
    clusters = payload.get("clusters") or []
    arche = (clusters[0].get("archetype") or {}) if clusters else {}
    radio_label = (
        f"Слушать радио {arche.get('emoji') or '🎵'} «{arche['name']}»"
        if arche.get("name") else "Слушать радио своего вкуса"
    )
    return {
        "id": "finale",
        "emoji": "✨",
        "title": pick("finale.title", ["Что дальше", "Куда дальше"]),
        "paragraphs": [
            pick("finale.p1", [
                "Вот и вся история — дальше можно не читать, а слушать.",
                "На этом слова заканчиваются и начинается музыка.",
                "Портрет дорисован. Теперь с ним можно поиграть.",
            ]),
            pick("finale.p2", [
                "Выбирай, куда пойдём: радио в твоём главном настроении, прогулка по карте вкуса или маленький квиз о тебе.",
                "Три двери на выбор: радио, карта, квиз. Ошибиться невозможно.",
            ]),
        ],
        "interactive": {
            "kind": "cta",
            "data": {"actions": [
                {"id": "radio", "label": radio_label},
                {"id": "dig", "label": "Копнуть карту вкуса"},
                {"id": "quiz", "label": "Пройти квиз «Угадай себя»"},
            ]},
        },
    }


_CHAPTER_BUILDERS = (
    _identity, _sound, _home_key, _rare_birds, _story_of_time, _clock_chapter, _finale,
)


def build_story(payload: dict, portrait_id: str) -> dict:
    """История портрета: {chapters: [...]} (SPEC v0.12 §A).

    payload — сохранённый портрет С уже собранной обсерваторией в
    payload["observatory"] (роут гарантирует). Главы без данных
    пропускаются; сбой одной главы не роняет историю целиком
    (логируется, глава пропускается) — история должна собираться
    у любого валидного портрета.
    """
    obs = payload.get("observatory") or {}
    pick = _picker(portrait_id)
    chapters = []
    for build in _CHAPTER_BUILDERS:
        try:
            chapter = build(payload, obs, pick)
        except Exception:
            log.exception("глава истории не собралась: %s", build.__name__)
            chapter = None
        if chapter:
            chapters.append(chapter)
    return {"chapters": chapters}

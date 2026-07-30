"""«История» (SPEC v0.12 §A) — БЕЗ сети, синтетические портреты.

Проверяем: share_to_phrase (границы полос), состав и пропуск глав по
данным, отсутствие запрещённого жаргона в сгенерированных текстах
НЕСКОЛЬКИХ портретов и portrait_id (регекс по списку из спеки),
детерминированность от portrait_id, роут: 404/409, ленивая сборка
в payload и идемпотентность (story_version=1).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import store
from app.engine.portrait import build_portrait
from app.engine.observatory import build_observatory
from app.engine.story import STORY_VERSION, build_story, share_to_phrase
from app.main import app
from app.routes import portrait as portrait_routes

client = TestClient(app)

CHAPTER_KEYS = {"id", "emoji", "title", "paragraphs", "interactive"}

# запрещённый жаргон из SPEC v0.12 (+ пара внутренних терминов на всякий)
FORBIDDEN_JARGON = re.compile(
    r"перцентил|кластер|PCA|энтроп|KDE|стандартизац|t-SNE|изоляционн",
    re.IGNORECASE,
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


# ------------------------------------------------------- синтетические данные

def _feature_row(i: int, calm: bool, liked_at: str | None) -> dict:
    """Строка признаков: две выраженные группы (как в test_observatory)."""
    rng = np.random.default_rng(42 + i)
    minor_score = float(rng.uniform(0.68, 0.85) if calm else rng.uniform(0.15, 0.32))
    return {
        "videoId": f"vid{i:03d}",
        "artists": f"Artist {i}",
        "title": f"Track {i}",
        "tempo": float(rng.uniform(65, 85) if calm else rng.uniform(150, 175)),
        "key": "A" if calm else "C",
        "mode": "minor" if calm else "major",
        "minor_score": round(minor_score, 3),
        "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),
        "percussive": float(rng.uniform(0.05, 0.15) if calm else rng.uniform(0.45, 0.6)),
        "energy_rms": float(rng.uniform(0.05, 0.1) if calm else rng.uniform(0.25, 0.35)),
        "brightness": float(rng.uniform(900, 1300) if calm else rng.uniform(2600, 3400)),
        "onset_rate": float(rng.uniform(0.8, 1.6) if calm else rng.uniform(4.0, 6.0)),
        "status": "ok",
        "liked_at": liked_at,
    }


def _full_payload() -> dict:
    """Портрет со ВСЕМИ данными для истории: timeline (первая половина —
    спокойные месяцы 1-3, вторая — яркие 4-6 => резкий перелом минора),
    clock (60 датированных лайков >= 50), тональности в meta."""
    rows = [
        _feature_row(
            i, calm=i < 30,
            liked_at=f"2024-{1 + i // 10:02d}-05T21:00:00+00:00",
        )
        for i in range(60)
    ]
    payload = build_portrait(pd.DataFrame(rows))
    payload["observatory"] = build_observatory(payload)
    return payload


def _sparse_payload() -> dict:
    """Портрет-минимум: без liked_at (нет timeline и clock) и без
    тональностей в meta (нет home_key)."""
    rows = [_feature_row(i, calm=i % 2 == 0, liked_at=None) for i in range(40)]
    df = pd.DataFrame(rows).drop(columns=["key", "mode"])
    payload = build_portrait(df)
    payload["observatory"] = build_observatory(payload)
    return payload


# ------------------------------------------------------------ share_to_phrase

@pytest.mark.parametrize("share,phrase", [
    (0.0, "считанные треки"),
    (-0.5, "считанные треки"),          # кламп снизу
    (0.029, "считанные треки"),
    (0.03, "несколько треков из ста"),
    (0.08, "примерно каждый десятый трек"),
    (0.15, "каждый пятый трек"),
    (0.22, "каждый четвёртый трек"),
    (0.28, "каждый третий трек"),
    (0.36, "каждый третий трек"),
    (0.37, "почти половина треков"),
    (0.42, "почти половина треков"),
    (0.45, "примерно половина треков"),
    (0.50, "примерно половина треков"),
    (0.55, "чуть больше половины треков"),
    (0.60, "два трека из трёх"),
    (0.70, "три трека из четырёх"),
    (0.93, "почти каждый трек"),
    (1.0, "почти каждый трек"),
    (1.5, "почти каждый трек"),          # кламп сверху
])
def test_share_to_phrase_bands_and_boundaries(share, phrase):
    assert share_to_phrase(share) == phrase


# ------------------------------------------------------------- состав глав

def test_full_portrait_has_all_chapters_in_order():
    story = build_story(_full_payload(), "abcdefgh")
    ids = [c["id"] for c in story["chapters"]]
    assert ids == [
        "identity", "sound", "home_key", "rare_birds",
        "story_of_time", "clock", "finale",
    ]
    for chapter in story["chapters"]:
        assert set(chapter) == CHAPTER_KEYS
        assert chapter["emoji"] and chapter["title"]
        assert 1 <= len(chapter["paragraphs"]) <= 3
        assert all(isinstance(p, str) and p for p in chapter["paragraphs"])
        assert chapter["interactive"] is None or "kind" in chapter["interactive"]


def test_sparse_portrait_skips_chapters_without_data():
    story = build_story(_sparse_payload(), "abcdefgh")
    ids = [c["id"] for c in story["chapters"]]
    assert "home_key" not in ids       # нет тональностей в meta
    assert "story_of_time" not in ids  # нет timeline
    assert "clock" not in ids          # нет liked_at
    assert ids[0] == "identity" and ids[-1] == "finale"
    assert "sound" in ids


def test_interactive_payloads_shapes():
    story = build_story(_full_payload(), "abcdefgh")
    by_id = {c["id"]: c for c in story["chapters"]}

    chips = by_id["identity"]["interactive"]
    assert chips["kind"] == "archetype_chips"
    archetypes = chips["data"]["archetypes"]
    assert archetypes and all(
        {"cluster", "name", "emoji", "share", "color"} <= set(a) for a in archetypes
    )

    play = by_id["sound"]["interactive"]
    assert play["kind"] == "play_track"
    assert play["data"]["label"]  # most_yours с videoId — играбелен

    home = by_id["home_key"]["interactive"]
    assert home["kind"] == "play_list"
    assert 1 <= len(home["data"]["items"]) <= 2
    assert all(i["label"] for i in home["data"]["items"])

    birds = by_id["rare_birds"]["interactive"]
    assert birds["kind"] == "play_list"
    assert 1 <= len(birds["data"]["items"]) <= 3

    assert by_id["story_of_time"]["interactive"]["kind"] == "timeline_mini"

    cta = by_id["finale"]["interactive"]
    assert cta["kind"] == "cta"
    assert [a["id"] for a in cta["data"]["actions"]] == ["radio", "dig", "quiz"]


def test_story_of_time_narrates_the_minor_turn():
    story = build_story(_full_payload(), "abcdefgh")
    time_chapter = next(c for c in story["chapters"] if c["id"] == "story_of_time")
    text = " ".join(time_chapter["paragraphs"])
    assert "минор" in text
    assert re.search(r"с \d+% до \d+%", text)


# ------------------------------------------------- честное покрытие (v0.13 §B)

def _identity_text(payload: dict) -> str:
    story = build_story(payload, "abcdefgh")
    identity = next(c for c in story["chapters"] if c["id"] == "identity")
    return " ".join(identity["paragraphs"])


def test_identity_mentions_coverage_below_85_percent():
    """Покрытие 306/437 (~70%) -> identity честно называет числа."""
    payload = _full_payload()
    payload["coverage"] = {"candidates": 437, "analyzed": 306}
    text = _identity_text(payload)
    assert "306 из 437" in text
    assert "превью" in text
    assert not FORBIDDEN_JARGON.search(text), text


@pytest.mark.parametrize("coverage", [
    {"candidates": 100, "analyzed": 85},   # ровно 85% — порог, фразы нет
    {"candidates": 100, "analyzed": 100},  # полное покрытие
    {"candidates": 0, "analyzed": 0},      # вырожденное — молча пропускаем
    None,                                  # старый payload без coverage
])
def test_identity_no_coverage_phrase_at_or_above_threshold(coverage):
    payload = _full_payload()
    if coverage is not None:
        payload["coverage"] = coverage
    text = _identity_text(payload)
    assert "спрятались" not in text and "смещён" not in text


# --------------------------------------------------------- запрещённый жаргон

def _user_texts(story: dict) -> list[str]:
    """Все строки, которые видит пользователь: заголовки, абзацы, кнопки."""
    texts = []
    for chapter in story["chapters"]:
        texts.append(chapter["title"])
        texts.extend(chapter["paragraphs"])
        interactive = chapter["interactive"] or {}
        data = interactive.get("data") or {}
        for action in data.get("actions", []):
            texts.append(action["label"])
    return texts


def test_no_forbidden_jargon_across_portraits_and_ids():
    """Регекс-тест из спеки: гоняем тексты нескольких синтетических
    портретов и нескольких portrait_id (разные id -> разные формулировки)."""
    payloads = [_full_payload(), _sparse_payload()]
    ids = ["aaaaaaaa", "bbbbbbbb", "hulxfwmp", "zzzzzzzz", "q2w3e4r5", "07070707"]
    checked = 0
    for payload in payloads:
        for portrait_id in ids:
            for text in _user_texts(build_story(payload, portrait_id)):
                assert not FORBIDDEN_JARGON.search(text), text
                checked += 1
    assert checked > 100  # тексты реально сгенерировались, а не пустые


# --------------------------------------------------------- детерминированность

def test_story_deterministic_for_same_portrait_id():
    payload = _full_payload()
    assert build_story(payload, "abcdefgh") == build_story(payload, "abcdefgh")


def test_variants_actually_vary_across_portrait_ids():
    """Хоть у каких-то двух id тексты различаются — вариативность живая."""
    payload = _full_payload()
    ids = ["aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd"]
    stories = {"\n".join(_user_texts(build_story(payload, i))) for i in ids}
    assert len(stories) > 1


# ------------------------------------------------------------------ роут API

def _saved_portrait_id() -> str:
    rows = [
        _feature_row(
            i, calm=i < 30,
            liked_at=f"2024-{1 + i // 10:02d}-05T21:00:00+00:00",
        )
        for i in range(60)
    ]
    return store.save_portrait(build_portrait(pd.DataFrame(rows)), source_label="демо")


def test_story_endpoint_computes_and_persists_in_payload():
    pid = _saved_portrait_id()
    r = client.get(f"/api/p/{pid}/story")
    assert r.status_code == 200
    story = r.json()
    assert [c["id"] for c in story["chapters"]][0] == "identity"
    assert [c["id"] for c in story["chapters"]][-1] == "finale"
    # ленивая одноразовая сборка легла в payload (и обсерватория тоже)
    row = store.get_portrait(pid)
    assert row["portrait"]["story_version"] == STORY_VERSION
    assert row["portrait"]["story"] == story
    assert isinstance(row["portrait"].get("observatory"), dict)


def test_story_endpoint_idempotent_second_get_from_payload(monkeypatch):
    pid = _saved_portrait_id()
    assert client.get(f"/api/p/{pid}/story").status_code == 200
    # маркер прямо в payload: второй GET обязан отдать его, а не пересчитать
    marker = {"chapters": [], "marker": "из payload, без пересчёта"}
    store.save_story(pid, marker, version=STORY_VERSION)

    def _boom(*args, **kwargs):
        raise AssertionError("второй GET не должен пересчитывать историю")

    monkeypatch.setattr(portrait_routes, "build_story", _boom)
    monkeypatch.setattr(portrait_routes, "build_observatory", _boom)
    r2 = client.get(f"/api/p/{pid}/story")
    assert r2.status_code == 200
    assert r2.json() == marker


def test_story_409_on_portrait_without_features():
    rows = [_feature_row(i, calm=i % 2 == 0, liked_at=None) for i in range(40)]
    payload = build_portrait(pd.DataFrame(rows))
    for p in payload["points"]:
        p.pop("features")
    pid = store.save_portrait(payload, source_label="демо")
    r = client.get(f"/api/p/{pid}/story")
    assert r.status_code == 409
    assert "старой версии" in r.json()["detail"]


def test_story_404_on_unknown_portrait():
    assert client.get("/api/p/zzzzzzzz/story").status_code == 404

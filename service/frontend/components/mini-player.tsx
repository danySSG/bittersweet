"use client";

/**
 * v0.9 (SPEC-v09-features §A): мини-плеер радио — ОДИН на приложение,
 * рендерится в PortraitView (верхний клиентский компонент портрета).
 * Фиксированная полоса внизу вьюпорта, появляется только при активной
 * очереди (player.queue !== null): эмодзи архетипа + «artist — title» +
 * prev/pause/next + тонкий прогресс 30-сек превью + крестик (стоп и скрыть).
 * Контент не перекрывает: при активном плеере body получает padding-bottom.
 *
 * v0.10 (SPEC-v10-critique §D): + переключатель «Превью 30с | Полностью».
 * В режиме «Полностью» слева живёт ВИДИМОЕ окошко YouTube-плеера ~120×68
 * (требование YouTube — не прятать в display:none): контейнер отдаётся хуку
 * через registerYtContainer, бейдж «полная версия · YouTube», пометки
 * пропуска/фолбэка (player.fullNote) — под названием трека.
 */

import { useEffect, useState } from "react";
import type { PreviewPlayer } from "@/lib/use-audio";

/** Высота полосы плеера — на неё отступает body, чтобы не прятать контент. */
const PLAYER_SPACE = "76px";
/** В режиме «Полностью» полоса выше — в ней видимое окно YT (68px). */
const PLAYER_SPACE_FULL = "104px";

/**
 * Тонкий прогресс текущего трека. Подписка на timeupdate <audio> (превью)
 * или поллинг YT-плеера (режим «Полностью») — внутри, чтобы обновления
 * перерисовывали только мини-плеер, а не весь портрет с картой и галактикой.
 */
function MiniProgress({
  player,
  trackKey,
  viaYt,
}: {
  player: PreviewPlayer;
  trackKey: string;
  viaYt: boolean;
}) {
  const [pct, setPct] = useState(0);

  useEffect(() => {
    setPct(0);
    if (viaYt) {
      // v0.10 §D: у YT-плеера нет timeupdate — поллим позицию дважды в секунду
      const id = window.setInterval(() => {
        const time = player.getYtTime();
        setPct(
          time !== null && time.duration > 0
            ? Math.min(100, (time.current / time.duration) * 100)
            : 0,
        );
      }, 500);
      return () => window.clearInterval(id);
    }
    const audio = player.getAudio();
    if (audio === null) return;
    const update = () => {
      const duration = audio.duration;
      setPct(
        Number.isFinite(duration) && duration > 0
          ? Math.min(100, (audio.currentTime / duration) * 100)
          : 0,
      );
    };
    update();
    audio.addEventListener("timeupdate", update);
    return () => audio.removeEventListener("timeupdate", update);
  }, [player, trackKey, viaYt]);

  return (
    <div className="mini-player-progress" aria-hidden>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function MiniPlayer({ player }: { player: PreviewPlayer }) {
  const { queue, queueIndex, queuePaused, mode, ytActive, fullNote } = player;
  const track =
    queue !== null && queueIndex >= 0 ? (queue[queueIndex] ?? null) : null;
  const active = track !== null;
  const full = mode === "full";

  // не перекрываем контент: отступ снизу у body, пока плеер на экране
  useEffect(() => {
    if (!active) return;
    const previous = document.body.style.paddingBottom;
    document.body.style.paddingBottom = full ? PLAYER_SPACE_FULL : PLAYER_SPACE;
    return () => {
      document.body.style.paddingBottom = previous;
    };
  }, [active, full]);

  if (queue === null || track === null) return null;

  return (
    <div className="mini-player" role="region" aria-label="Мини-плеер радио">
      <MiniProgress
        player={player}
        trackKey={track.url ?? track.videoId ?? String(queueIndex)}
        viaYt={ytActive}
      />
      {/* v0.10 §D: видимое окно официального YouTube-плеера (НЕ display:none).
          Контейнер существует только в режиме «Полностью» — снятие рефа
          сносит плеер в хуке. */}
      {full && (
        <div className="mini-player-yt" aria-label="Окно YouTube-плеера">
          <div className="mini-player-yt-host" ref={player.registerYtContainer} />
        </div>
      )}
      <span className="mini-player-emoji" aria-hidden>
        {track.meta ?? "📻"}
      </span>
      <span className="mini-player-body">
        <span className="mini-player-label" title={track.label}>
          {track.label}
        </span>
        {/* пометка режима «Полностью»: фолбэк/пропуск или бейдж источника */}
        {full && (
          <span
            className={
              fullNote !== null
                ? "mini-player-note mini-player-note-warn"
                : "mini-player-note"
            }
            role="status"
          >
            {fullNote ?? "полная версия · YouTube"}
          </span>
        )}
      </span>
      <span className="mini-player-pos mono">
        {queueIndex + 1}/{queue.length}
      </span>
      {/* v0.10 §D: переключатель режима «Превью 30с | Полностью» */}
      <div
        className="mini-player-mode"
        role="group"
        aria-label="Режим воспроизведения"
      >
        <button
          type="button"
          className={!full ? "mode-tab mode-tab-active" : "mode-tab"}
          aria-pressed={!full}
          onClick={() => player.setMode("preview")}
        >
          Превью 30с
        </button>
        <button
          type="button"
          className={full ? "mode-tab mode-tab-active" : "mode-tab"}
          aria-pressed={full}
          onClick={() => player.setMode("full")}
        >
          Полностью
        </button>
      </div>
      <div className="mini-player-controls">
        <button
          type="button"
          className="play-btn mini-player-btn"
          onClick={player.queuePrev}
          aria-label="Предыдущий трек"
        >
          ⏮︎
        </button>
        <button
          type="button"
          className="play-btn mini-player-btn"
          onClick={player.queueTogglePause}
          aria-label={queuePaused ? "Продолжить" : "Пауза"}
        >
          {queuePaused ? "▶︎" : "⏸︎"}
        </button>
        <button
          type="button"
          className="play-btn mini-player-btn"
          onClick={player.queueNext}
          aria-label="Следующий трек"
        >
          ⏭︎
        </button>
      </div>
      <button
        type="button"
        className="play-btn mini-player-btn mini-player-close"
        onClick={player.stopQueue}
        aria-label="Остановить радио и скрыть плеер"
      >
        ✕
      </button>
    </div>
  );
}

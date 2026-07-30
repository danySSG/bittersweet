"use client";

/**
 * v0.11: кнопка ▶ превью + ленивый батч-резолвер — вынесены из
 * components/portrait-view.tsx БЕЗ изменений поведения, потому что теперь
 * они нужны и обсерватории (белые вороны, SPEC-v11-observatory §B6),
 * а импорт portrait-view → observatory → portrait-view дал бы цикл.
 *
 * История: v0.6 (SPEC-v06-ux §B3) — ленивый resolve превью;
 * v0.10 (SPEC-v10-critique §D) — videoId для режима «Полностью».
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { resolvePreviews } from "@/lib/api";
import { splitTrackLabel, type PreviewPlayer } from "@/lib/use-audio";

/** Состояние кнопки ▶ у одной строки "artist — title". */
export type PreviewButtonState =
  | { kind: "ready"; url: string } // URL известен — обычный ▶/⏸
  | { kind: "lazy" } // URL неизвестен, но можно дорезолвить по клику
  | { kind: "resolving" } // батч /api/preview/resolve в полёте
  | { kind: "none" }; // превью нет (resolve вернул null / бэк старый)

/** Всё, что нужно строке, чтобы нарисовать кнопку превью. */
export interface PreviewContext {
  player: PreviewPlayer;
  stateFor: (label: string, known?: string | null) => PreviewButtonState;
  onLazy: (label: string) => void;
}

/**
 * Ленивый резолв превью для строк без preview_url (SPEC-v06-ux §B3):
 * первый клик по «пустой» кнопке шлёт ОДИН батч POST /api/preview/resolve
 * на все видимые кандидаты (≤24), кнопки появляются по мере разрешения;
 * null — кнопка исчезает (ряд не дизейблим). Старый бэк без роута (404) —
 * молча прячем ленивые кнопки.
 */
export function usePreviewResolver(
  labels: string[],
  player: PreviewPlayer,
): Pick<PreviewContext, "stateFor" | "onLazy"> {
  const [resolved, setResolved] = useState<Map<string, string | null>>(
    () => new Map(),
  );
  const [status, setStatus] = useState<
    "idle" | "loading" | "done" | "failed"
  >("idle");
  // ref-дубль статуса: защита от двойного батча при быстрых кликах
  const statusRef = useRef(status);

  const candidates = useMemo(() => new Set(labels), [labels]);

  const resolve = useCallback(async (): Promise<Map<
    string,
    string | null
  > | null> => {
    if (statusRef.current !== "idle" || labels.length === 0) return null;
    statusRef.current = "loading";
    setStatus("loading");
    try {
      const { urls } = await resolvePreviews(labels.map(splitTrackLabel));
      const map = new Map<string, string | null>();
      labels.forEach((label, i) => {
        const url = urls[i];
        map.set(label, typeof url === "string" && url !== "" ? url : null);
      });
      statusRef.current = "done";
      setResolved(map);
      setStatus("done");
      return map;
    } catch {
      // 404 старого бэка / сеть — деградируем молча, кнопки исчезают
      statusRef.current = "failed";
      setStatus("failed");
      return null;
    }
  }, [labels]);

  const onLazy = useCallback(
    (label: string) => {
      void resolve().then((map) => {
        // кликнутый трек сразу включаем, если превью нашлось
        const url = map?.get(label);
        if (typeof url === "string" && url !== "") player.toggle(url);
      });
    },
    [resolve, player],
  );

  const stateFor = useCallback(
    (label: string, known?: string | null): PreviewButtonState => {
      if (typeof known === "string" && known !== "") {
        return { kind: "ready", url: known };
      }
      const url = resolved.get(label);
      if (url !== undefined) {
        return url !== null ? { kind: "ready", url } : { kind: "none" };
      }
      if (!candidates.has(label)) return { kind: "none" };
      if (status === "loading") return { kind: "resolving" };
      if (status === "idle") return { kind: "lazy" };
      return { kind: "none" }; // done без записи / failed
    },
    [resolved, status, candidates],
  );

  return { stateFor, onLazy };
}

/** Кнопка ▶/⏸ (или её ленивый/скрытый вариант) у строки трека. */
export function PreviewPlayButton({
  label,
  state,
  player,
  onLazy,
  videoId,
}: {
  label: string;
  state: PreviewButtonState;
  player: PreviewPlayer;
  onLazy: (label: string) => void;
  /** v0.10 §D: videoId строки — кнопка ▶ уважает режим «Полностью» */
  videoId?: string | null;
}) {
  if (state.kind === "none") return null;
  if (state.kind === "resolving") {
    return (
      <button
        type="button"
        className="play-btn play-btn-mini"
        disabled
        aria-label="Ищем превью…"
      >
        <span className="btn-spinner" aria-hidden />
      </button>
    );
  }
  if (state.kind === "lazy") {
    return (
      <button
        type="button"
        className="play-btn play-btn-mini play-btn-lazy"
        title="найти и включить превью"
        aria-label={`Найти превью: ${label}`}
        onClick={() => onLazy(label)}
      >
        ▶︎
      </button>
    );
  }
  const playing = player.playingUrl === state.url;
  const url = state.url;
  return (
    <button
      type="button"
      className="play-btn play-btn-mini"
      aria-label={playing ? "Пауза" : `Слушать превью: ${label}`}
      // v0.10 §D: videoId в extras — в режиме «Полностью» toggle играет
      // полный трек через видимый YT-плеер мини-плеера
      onClick={() => player.toggle(url, { videoId, label })}
    >
      {playing ? "⏸︎" : "▶︎"}
    </button>
  );
}

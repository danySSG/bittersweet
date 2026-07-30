"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

/**
 * Поле «Ссылка на плейлист YouTube Music» на лендинге.
 * Клиентская валидация: ссылка должна содержать параметр list=,
 * дальше — переход на /portrait?playlist={url}, где живёт джоба.
 */
export default function PlaylistForm() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [hint, setHint] = useState<string | null>(null);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = url.trim();
    if (trimmed === "") {
      setHint("Вставь ссылку на плейлист YouTube Music.");
      return;
    }
    if (!trimmed.includes("list=")) {
      setHint(
        "В ссылке нет параметра list= — открой плейлист в YouTube Music и скопируй адрес целиком.",
      );
      return;
    }
    setHint(null);
    router.push(`/portrait?playlist=${encodeURIComponent(trimmed)}`);
  }

  return (
    <form className="playlist-form" onSubmit={submit} noValidate>
      <label className="playlist-label" htmlFor="playlist-url">
        Ссылка на плейлист YouTube Music
      </label>
      <div className="playlist-row">
        <input
          id="playlist-url"
          className="playlist-input"
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://music.youtube.com/playlist?list=PLfoNjwA6k…"
          value={url}
          onChange={(event) => {
            setUrl(event.target.value);
            if (hint !== null) setHint(null);
          }}
        />
        <button type="submit" className="btn btn-primary">
          Построить портрет
        </button>
      </div>
      {hint !== null && (
        <p className="playlist-hint" role="alert">
          {hint}
        </p>
      )}
    </form>
  );
}

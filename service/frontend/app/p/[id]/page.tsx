"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ApiError, fetchSavedPortrait } from "@/lib/api";
import type { SavedPortrait } from "@/lib/types";
import PortraitView from "@/components/portrait-view";

type PageStatus = "loading" | "ready" | "notfound" | "error";

function formatDate(iso: string | undefined): string | null {
  if (iso === undefined || iso === "") return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export default function SavedPortraitPage() {
  const params = useParams<{ id: string }>();
  const rawId = params?.id;
  const id = typeof rawId === "string" ? rawId : "";

  const [portrait, setPortrait] = useState<SavedPortrait | null>(null);
  const [status, setStatus] = useState<PageStatus>("loading");
  const [message, setMessage] = useState<string>("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    void attempt;
    if (id === "") {
      setStatus("notfound");
      return;
    }
    let cancelled = false;
    setStatus("loading");
    fetchSavedPortrait(id)
      .then((data) => {
        if (cancelled) return;
        setPortrait(data);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setStatus("notfound");
          return;
        }
        setMessage(
          err instanceof Error ? err.message : "Неизвестная ошибка.",
        );
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [id, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  let content: React.ReactNode;
  if (status === "loading") {
    content = (
      <div className="state-box">
        <div className="spinner" aria-hidden />
        <h2>Открываем портрет…</h2>
        <p>Достаём сохранённый портрет по постоянной ссылке.</p>
      </div>
    );
  } else if (status === "notfound") {
    content = (
      <div className="state-box">
        <h2>Портрет не найден</h2>
        <p>
          Портрет не найден — возможно, ссылка неполная. Проверь, что
          скопировал адрес целиком.
        </p>
        <div className="state-actions">
          <Link href="/" className="btn btn-primary">
            Построить свой
          </Link>
        </div>
      </div>
    );
  } else if (status === "error" || portrait === null) {
    content = (
      <div className="state-box">
        <h2>Не получилось загрузить портрет</h2>
        <p>{message !== "" ? message : "Пустой ответ бэкенда."}</p>
        <div className="state-actions">
          <button type="button" className="btn btn-primary" onClick={retry}>
            Попробовать снова
          </button>
          <Link href="/" className="btn btn-ghost">
            На главную
          </Link>
        </div>
      </div>
    );
  } else {
    const date = formatDate(portrait.created_at);
    const source = portrait.source_label ?? "сохранённый портрет";
    content = (
      <>
        <p className="saved-badge mono">
          портрет · {source}
          {date !== null ? ` · ${date}` : ""}
        </p>
        <PortraitView
          portrait={portrait}
          title="Акустический портрет"
          portraitId={portrait.id ?? id}
          savedDiscoveries={portrait.discoveries}
          // v0.12 §B: новому зрителю пермалинка — сначала рассказ
          defaultView="story"
          headerExtra={
            <Link href="/" className="btn btn-primary btn-small">
              Построить свой
            </Link>
          }
        />
      </>
    );
  }

  return (
    <main className="portrait-page container">
      <Link href="/" className="back-link">
        ← Bittersweet
      </Link>
      {content}
    </main>
  );
}

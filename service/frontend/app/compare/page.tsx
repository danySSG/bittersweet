"use client";

import Link from "next/link";
import { useState } from "react";
import { ApiError, fetchCompare } from "@/lib/api";
import type { CompareFacet, CompareResult } from "@/lib/types";

/**
 * Сравнение вкусов (SPEC-v03-max, D.2): два поля «ссылка или ID портрета»
 * → GET /api/compare?a=&b= → score с вердиктом, общие архетипы, facets.
 */

/** Словарь эмодзи архетипов (бренд-язык из раздела A спеки). Матчим по префиксу:
 *  дедуп добавляет суффикс различия («грустный бэнгер · ~162 bpm»). */
const ARCHETYPE_EMOJI: Array<[string, string]> = [
  ["биттерсвит", "🍬"],
  ["грустный бэнгер", "🌒"],
  ["тихая грусть", "🌫️"],
  ["светлая сторона", "☀️"],
  ["грув", "🕺"],
  ["тёмная материя", "🌑"],
  ["между строк", "🌗"],
];

function archetypeEmoji(name: string): string {
  const lower = name.toLowerCase();
  for (const [prefix, emoji] of ARCHETYPE_EMOJI) {
    if (lower.startsWith(prefix)) return emoji;
  }
  return "🎧";
}

/** Принимаем и /p/{id}-URL, и голый id. */
function parsePortraitId(input: string): string | null {
  const trimmed = input.trim();
  if (trimmed === "") return null;
  const urlMatch = trimmed.match(/\/p\/([A-Za-z0-9]+)/);
  if (urlMatch !== null) return urlMatch[1];
  if (/^[A-Za-z0-9]+$/.test(trimmed)) return trimmed;
  return null;
}

function describeCompareError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404) {
      return "Один из портретов не найден — проверь, что ссылки полные и портреты сохранены.";
    }
    if (err.status === 422) {
      return "Это один и тот же портрет — сравни себя с другом, а не с собой.";
    }
    return err.message;
  }
  return err instanceof Error ? err.message : "Что-то пошло не так.";
}

function FacetBars({ facet }: { facet: CompareFacet }) {
  const max = Math.max(facet.a, facet.b, 1);
  const widthA = Math.max(2, (facet.a / max) * 100);
  const widthB = Math.max(2, (facet.b / max) * 100);
  return (
    <div className="facet">
      <div className="facet-name">{facet.name}</div>
      <div className="facet-row">
        <span className="facet-side mono">A</span>
        <div className="facet-track">
          <div className="facet-fill facet-fill-a" style={{ width: `${widthA}%` }} />
        </div>
        <span className="facet-value mono">
          {facet.a} {facet.unit}
        </span>
      </div>
      <div className="facet-row">
        <span className="facet-side mono">B</span>
        <div className="facet-track">
          <div className="facet-fill facet-fill-b" style={{ width: `${widthB}%` }} />
        </div>
        <span className="facet-value mono">
          {facet.b} {facet.unit}
        </span>
      </div>
    </div>
  );
}

function CompareResultView({ result }: { result: CompareResult }) {
  const common = result.common_archetypes ?? [];
  const facets = result.facets ?? [];
  return (
    <section className="compare-result" aria-live="polite">
      <div className="compare-score-card">
        <div className="compare-score">
          <span className="compare-score-value mono">{result.score}</span>
          <span className="compare-score-max mono">/ 100</span>
        </div>
        <p className="compare-verdict">{result.verdict}</p>
      </div>

      {common.length > 0 && (
        <>
          <h2 className="section-title">Общие архетипы</h2>
          <div className="chip-row">
            {common.map((name) => (
              <span className="archetype-chip" key={name}>
                <span aria-hidden>{archetypeEmoji(name)}</span> {name}
              </span>
            ))}
          </div>
        </>
      )}

      {facets.length > 0 && (
        <>
          <h2 className="section-title">Грань к грани</h2>
          <div className="facet-list">
            {facets.map((facet) => (
              <FacetBars key={facet.name} facet={facet} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

export default function ComparePage() {
  const [inputA, setInputA] = useState("");
  const [inputB, setInputB] = useState("");
  const [hint, setHint] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<CompareResult | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const a = parsePortraitId(inputA);
    const b = parsePortraitId(inputB);
    if (a === null || b === null) {
      setHint(
        "Вставь в оба поля ссылку вида /p/abc123 или голый ID портрета.",
      );
      return;
    }
    if (a === b) {
      setHint("Это один и тот же портрет — нужны два разных.");
      return;
    }
    setHint(null);
    setLoading(true);
    setResult(null);
    try {
      setResult(await fetchCompare(a, b));
    } catch (err) {
      setHint(describeCompareError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="portrait-page container">
      <Link href="/" className="back-link">
        ← Bittersweet
      </Link>
      <header className="portrait-header">
        <h1>Совместимость по звуку</h1>
      </header>
      <p className="portrait-meta">
        Вставь две постоянные ссылки на портреты — сравним темп, минор, яркость
        и архетипы.
      </p>

      <form className="compare-form" onSubmit={(e) => void submit(e)} noValidate>
        <div className="compare-fields">
          <label className="compare-field">
            <span className="playlist-label">Портрет A — ссылка или ID</span>
            <input
              className="playlist-input"
              type="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://…/p/abc123 или abc123"
              value={inputA}
              onChange={(e) => {
                setInputA(e.target.value);
                if (hint !== null) setHint(null);
              }}
            />
          </label>
          <label className="compare-field">
            <span className="playlist-label">Портрет B — ссылка или ID</span>
            <input
              className="playlist-input"
              type="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://…/p/xyz789 или xyz789"
              value={inputB}
              onChange={(e) => {
                setInputB(e.target.value);
                if (hint !== null) setHint(null);
              }}
            />
          </label>
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading && <span className="btn-spinner" aria-hidden />}
          {loading ? "Сравниваем…" : "Сравнить вкус"}
        </button>
        {hint !== null && (
          <p className="playlist-hint" role="alert">
            {hint}
          </p>
        )}
      </form>

      {result !== null && <CompareResultView result={result} />}
    </main>
  );
}

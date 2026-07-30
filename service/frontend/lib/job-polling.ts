/**
 * Общий поллинг джоб (v0.2 плейлист / v0.2b Takeout / v0.4 discovery).
 * Вынесен из app/portrait/page.tsx, чтобы discovery не копипастил цикл.
 */

import { fetchJob } from "./api";
import type { Job } from "./types";

export const POLL_INTERVAL_MS = 2_000;
export const POLL_TIMEOUT_MS = 8 * 60 * 1_000;

/** Джоба не завершилась за timeoutMs — поллинг остановлен. */
export class JobTimeoutError extends Error {
  constructor() {
    super("Джоба не завершилась вовремя.");
    this.name = "JobTimeoutError";
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Поллит GET /api/jobs/{id}, пока джоба не станет done|error.
 *
 * - onUpdate — каждый свежий снапшот (для прогресс-бара);
 * - isCancelled — мягкая отмена (unmount): вернёт null, ничего не бросая;
 * - бросает ApiError (сеть/HTTP) и JobTimeoutError (истёк timeoutMs).
 */
export async function pollJob(
  jobId: string,
  opts: {
    onUpdate?: (job: Job) => void;
    isCancelled?: () => boolean;
    intervalMs?: number;
    timeoutMs?: number;
  } = {},
): Promise<Job | null> {
  const intervalMs = opts.intervalMs ?? POLL_INTERVAL_MS;
  const timeoutMs = opts.timeoutMs ?? POLL_TIMEOUT_MS;
  const startedAt = Date.now();

  for (;;) {
    if (opts.isCancelled?.() === true) return null;
    if (Date.now() - startedAt >= timeoutMs) throw new JobTimeoutError();
    const job = await fetchJob(jobId);
    if (opts.isCancelled?.() === true) return null;
    opts.onUpdate?.(job);
    if (job.status === "done" || job.status === "error") return job;
    // queued | running — ждём и спрашиваем снова
    await sleep(intervalMs);
  }
}

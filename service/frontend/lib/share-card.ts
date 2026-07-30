import type { Cluster, Portrait } from "./types";
import { clusterColor } from "./colors";

/**
 * Share-card (SPEC-v03-max, D.3): PNG 1080×1920, рисуется канвасом на клиенте
 * в offscreen document.createElement('canvas'). Вверху — эмодзи + имя
 * топ-архетипа, мини-скаттер, топ-3 архетипа с долями, биттерсвит-%,
 * водяной знак Bittersweet + /p/{id} (если есть). Без внешних зависимостей.
 */

const W = 1080;
const H = 1920;

const BG = "#0b0b10";
const BG_RAISED = "#13131c";
const BORDER = "#23232f";
const TEXT = "#ececf4";
const TEXT_SECONDARY = "#8a8aa0";
const ACCENT = "#7f77dd";

const SANS =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const MONO =
  'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace';

function roundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Подбирает размер шрифта, чтобы текст влез в maxWidth. Возвращает размер. */
function fitFont(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  baseSize: number,
  weight: string,
  family: string,
): number {
  let size = baseSize;
  ctx.font = `${weight} ${size}px ${family}`;
  while (size > 26 && ctx.measureText(text).width > maxWidth) {
    size -= 4;
    ctx.font = `${weight} ${size}px ${family}`;
  }
  return size;
}

/** Кластеры по убыванию доли — «топ-архетипы». */
function clustersByShare(portrait: Portrait): Cluster[] {
  return [...portrait.clusters].sort((a, b) => b.share - a.share);
}

function drawScatterPanel(
  ctx: CanvasRenderingContext2D,
  portrait: Portrait,
  x: number,
  y: number,
  w: number,
  h: number,
): void {
  ctx.fillStyle = BG_RAISED;
  roundedRect(ctx, x, y, w, h, 28);
  ctx.fill();
  ctx.strokeStyle = BORDER;
  ctx.lineWidth = 2;
  roundedRect(ctx, x, y, w, h, 28);
  ctx.stroke();

  const pad = 48;
  const plotX = x + pad;
  const plotY = y + pad;
  const plotW = w - pad * 2;
  const plotH = h - pad * 2;

  // сетка по квартилям
  ctx.save();
  ctx.strokeStyle = BORDER;
  ctx.lineWidth = 2;
  ctx.setLineDash([6, 10]);
  for (const q of [25, 50, 75]) {
    const gx = plotX + (q / 100) * plotW;
    const gy = plotY + plotH - (q / 100) * plotH;
    ctx.beginPath();
    ctx.moveTo(gx, plotY);
    ctx.lineTo(gx, plotY + plotH);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(plotX, gy);
    ctx.lineTo(plotX + plotW, gy);
    ctx.stroke();
  }
  ctx.restore();

  // точки
  const points = portrait.points ?? [];
  for (const p of points) {
    const px = plotX + (p.x / 100) * plotW;
    const py = plotY + plotH - (p.y / 100) * plotH;
    ctx.globalAlpha = 0.85;
    ctx.fillStyle = clusterColor(portrait.clusters, p.cluster);
    ctx.beginPath();
    ctx.arc(px, py, 7, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;

  // мини-подписи осей
  ctx.fillStyle = TEXT_SECONDARY;
  ctx.font = `24px ${MONO}`;
  ctx.textAlign = "left";
  ctx.fillText("грустнее", plotX, y + h - 16);
  ctx.textAlign = "right";
  ctx.fillText("радостнее →", plotX + plotW, y + h - 16);
}

/** Рисует карточку и отдаёт PNG-блоб через toBlob. */
export async function renderShareCard(
  portrait: Portrait,
  portraitId: string | null,
): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (ctx === null) {
    throw new Error("Canvas 2D недоступен в этом браузере.");
  }

  // фон + мягкое свечение
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, W, H);
  const glowTop = ctx.createRadialGradient(W / 2, 320, 0, W / 2, 320, 880);
  glowTop.addColorStop(0, "rgba(127, 119, 221, 0.22)");
  glowTop.addColorStop(1, "rgba(127, 119, 221, 0)");
  ctx.fillStyle = glowTop;
  ctx.fillRect(0, 0, W, H);
  const glowBottom = ctx.createRadialGradient(160, 1560, 0, 160, 1560, 720);
  glowBottom.addColorStop(0, "rgba(95, 179, 161, 0.10)");
  glowBottom.addColorStop(1, "rgba(95, 179, 161, 0)");
  ctx.fillStyle = glowBottom;
  ctx.fillRect(0, 0, W, H);

  // бренд
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = ACCENT;
  ctx.font = `600 40px ${MONO}`;
  ctx.fillText("T A S T E M A P", W / 2, 130);

  // топ-архетип: эмодзи + имя (защита от старого бэка без archetype)
  const sorted = clustersByShare(portrait);
  const top = sorted[0];
  const topEmoji = top?.archetype?.emoji ?? "🎧";
  const topName = top?.archetype?.name ?? top?.label ?? "портрет вкуса";

  ctx.font = `150px ${SANS}`;
  ctx.fillText(topEmoji, W / 2, 390);

  ctx.fillStyle = TEXT;
  const nameSize = fitFont(ctx, topName, 920, 78, "800", SANS);
  ctx.font = `800 ${nameSize}px ${SANS}`;
  ctx.fillText(topName, W / 2, 520);

  ctx.fillStyle = TEXT_SECONDARY;
  ctx.font = `34px ${SANS}`;
  ctx.fillText(
    `акустический портрет · ${portrait.n_tracks} треков`,
    W / 2,
    580,
  );

  // мини-скаттер
  drawScatterPanel(ctx, portrait, 100, 640, 880, 540);

  // топ-3 архетипа с долями
  ctx.fillStyle = TEXT_SECONDARY;
  ctx.font = `600 30px ${MONO}`;
  ctx.textAlign = "left";
  ctx.fillText("ТОП АРХЕТИПЫ", 100, 1268);

  const rows = sorted.slice(0, 3);
  rows.forEach((cluster, i) => {
    const rowY = 1330 + i * 116;
    const emoji = cluster.archetype?.emoji ?? "🎵";
    const name = cluster.archetype?.name ?? cluster.label;
    const color =
      cluster.color ?? clusterColor(portrait.clusters, i);

    ctx.textAlign = "left";
    ctx.font = `52px ${SANS}`;
    ctx.fillStyle = TEXT;
    ctx.fillText(emoji, 100, rowY);

    const rowNameSize = fitFont(ctx, name, 560, 44, "700", SANS);
    ctx.font = `700 ${rowNameSize}px ${SANS}`;
    ctx.fillStyle = TEXT;
    ctx.fillText(name, 190, rowY - 4);

    ctx.textAlign = "right";
    ctx.font = `600 44px ${MONO}`;
    ctx.fillStyle = TEXT;
    ctx.fillText(`${cluster.share}%`, 980, rowY - 4);

    // полоса доли
    const barY = rowY + 20;
    ctx.fillStyle = BORDER;
    roundedRect(ctx, 100, barY, 880, 12, 6);
    ctx.fill();
    const fillW = Math.max(12, (Math.min(100, Math.max(0, cluster.share)) / 100) * 880);
    ctx.fillStyle = color;
    roundedRect(ctx, 100, barY, fillW, 12, 6);
    ctx.fill();
  });

  // биттерсвит
  const bsY = 1330 + rows.length * 116 + 60;
  ctx.textAlign = "left";
  ctx.fillStyle = ACCENT;
  ctx.font = `800 88px ${MONO}`;
  ctx.fillText(`${portrait.bittersweet.share}%`, 100, bsY);
  const bsLabelX = 100 + ctx.measureText(`${portrait.bittersweet.share}%`).width + 28;
  ctx.fillStyle = TEXT;
  ctx.font = `600 40px ${SANS}`;
  ctx.fillText("биттерсвит", bsLabelX, bsY);
  const percentile = portrait.bittersweet.percentile;
  if (typeof percentile === "number") {
    ctx.fillStyle = TEXT_SECONDARY;
    ctx.font = `30px ${SANS}`;
    ctx.fillText(
      `биттерсвитнее, чем у ${percentile}% портретов`,
      100,
      bsY + 52,
    );
  }

  // водяной знак
  ctx.textAlign = "center";
  ctx.fillStyle = TEXT_SECONDARY;
  ctx.font = `32px ${MONO}`;
  ctx.fillText(
    portraitId !== null && portraitId !== ""
      ? `Bittersweet · /p/${portraitId}`
      : "Bittersweet",
    W / 2,
    H - 72,
  );

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob !== null) {
        resolve(blob);
      } else {
        reject(new Error("Не удалось собрать PNG из канваса."));
      }
    }, "image/png");
  });
}

/** Скачивает блоб как файл (ссылка + click, без зависимостей). */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

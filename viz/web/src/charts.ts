import {
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type Time,
} from "lightweight-charts";

export type ChartHandle = { remove: () => void };

export function token(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function baseChart(el: HTMLElement, height: number): IChartApi {
  el.replaceChildren();
  const bg = token("--card", "#0b1220");
  const text = token("--muted", "#94a3b8");
  const line = token("--line", "#1e293b");
  return createChart(el, {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: bg }, textColor: text },
    grid: { vertLines: { color: line }, horzLines: { color: line } },
    width: el.clientWidth || 640,
    height: el.clientHeight || height,
    timeScale: { borderColor: line, fixLeftEdge: true, fixRightEdge: true },
    rightPriceScale: { borderColor: line },
    // Mobile: a touch pan can shove the series off-screen so the pane looks empty.
    handleScroll: false,
    handleScale: false,
  });
}

function attachResize(chart: IChartApi, el: HTMLElement): ChartHandle {
  const fit = () => {
    if (el.clientWidth > 0) chart.timeScale().fitContent();
  };
  const ro = new ResizeObserver(() => fit());
  ro.observe(el);
  requestAnimationFrame(fit);
  return {
    remove() {
      ro.disconnect();
      chart.remove();
    },
  };
}

/** Floor ISO/RFC3339 (incl. 6-digit micros) to UTC unix seconds. Safari-safe. */
export function parseUtcSeconds(t: string): number | null {
  const ms = Date.parse(t);
  if (Number.isFinite(ms)) return Math.floor(ms / 1000);
  const m = t.match(
    /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?:Z|([+-])(\d{2}):?(\d{2}))?/,
  );
  if (!m) return null;
  const frac = m[7] ? Number((m[7] + "000").slice(0, 3)) : 0;
  let utc = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], frac);
  if (m[8] && m[9] != null) {
    const sign = m[8] === "-" ? -1 : 1;
    utc -= sign * ((+m[9] || 0) * 3600 + (+m[10] || 0) * 60) * 1000;
  }
  return Math.floor(utc / 1000);
}

/** Unique, strictly increasing unix seconds — lightweight-charts drops the series otherwise. */
export function toLine(points: { t: string; v: number }[]) {
  const byT = new Map<number, number>();
  for (const p of points) {
    if (!Number.isFinite(p.v)) continue;
    const t = parseUtcSeconds(p.t);
    if (t == null) continue;
    byT.set(t, p.v);
  }
  return [...byT.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time: time as Time, value }));
}

export function lineChart(el: HTMLElement, points: { t: string; v: number }[], color: string): ChartHandle {
  const chart = baseChart(el, 280);
  chart.addSeries(LineSeries, { color, lineWidth: 2 }).setData(toLine(points));
  chart.timeScale().fitContent();
  return attachResize(chart, el);
}

export function birthReturnChart(
  el: HTMLElement,
  seriesA: { start: string; return_pct: number }[],
  seriesB: { start: string; return_pct: number }[],
  colorA: string,
  colorB: string,
): ChartHandle {
  const chart = baseChart(el, 320);
  const asLine = (rows: { start: string; return_pct: number }[]) =>
    toLine(rows.map((r) => ({ t: r.start + "T00:00:00Z", v: r.return_pct })));
  chart.addSeries(LineSeries, { color: colorA, lineWidth: 2 }).setData(asLine(seriesA));
  if (seriesB.length) {
    chart.addSeries(LineSeries, { color: colorB, lineWidth: 2 }).setData(asLine(seriesB));
  }
  chart.timeScale().fitContent();
  return attachResize(chart, el);
}

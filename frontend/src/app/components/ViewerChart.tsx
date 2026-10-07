import { useEffect, useRef, useState } from "react";
import { axisNumber, dayLabel } from "../lib/format";

interface ViewerChartProps {
  /** One point per day the channel was seen live: that day's mean viewers. */
  history: { date: string; viewers: number }[];
  /** Every day in the window, in the same "Aug 13" form, or null if unknown. */
  days: string[] | null;
  color: string;
  label: string;
}

/** Round up to 1, 2, 2.5 or 5 × 10ⁿ, so the axis has about four steps. */
function niceStep(max: number): number {
  const raw = Math.max(1, max) / 4;
  const base = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].find((m) => m * base >= raw) ?? 10;
  return step * base;
}

/**
 * Daily mean concurrent viewers across the window. Days the channel was never
 * seen live are hollow markers on the baseline, and the line breaks there
 * rather than drawing through a day with no reading.
 */
export function ViewerChart({ history, days, color, label }: ViewerChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  const height = width >= 560 ? 240 : 200;

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !width) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    // The window's days when known, otherwise just the days that have data.
    const seen = new Map(history.map((p) => [p.date, p.viewers]));
    const axis = days && history.every((p) => days.includes(p.date)) ? days : history.map((p) => p.date);
    const series = axis.map((day) => seen.get(day) ?? null);

    const L = 58, R = 20, T = 18, B = 34;
    const step = niceStep(Math.max(0, ...history.map((p) => p.viewers)));
    const max = Math.ceil(Math.max(1, ...history.map((p) => p.viewers)) / step) * step;
    const span = Math.max(1, axis.length - 1);
    const X = (i: number) => (axis.length === 1 ? (L + width - R) / 2 : L + (i * (width - L - R)) / span);
    const Y = (v: number) => height - B - (v / max) * (height - T - B);

    ctx.font = '11px "IBM Plex Mono", monospace';
    ctx.fillStyle = "#5d5850";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    ctx.lineWidth = 1;
    for (let v = 0; v <= max; v += step) {
      ctx.strokeStyle = v ? "#eeebe4" : "#1c1b19";
      ctx.beginPath();
      ctx.moveTo(L, Math.round(Y(v)) + 0.5);
      ctx.lineTo(width - R, Math.round(Y(v)) + 0.5);
      ctx.stroke();
      ctx.fillText(axisNumber(v), L - 8, Y(v));
    }

    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.strokeStyle = "#1c1b19";
    const ticks = axis.length > 2 ? [0, Math.floor((axis.length - 1) / 2), axis.length - 1] : axis.map((_, i) => i);
    for (const i of ticks) {
      const x = Math.round(X(i)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, height - B);
      ctx.lineTo(x, height - B + 4);
      ctx.stroke();
      // The end labels are centred on ticks near the edge; keep them inside.
      const text = dayLabel(axis[i]);
      const half = ctx.measureText(text).width / 2;
      ctx.fillText(text, Math.min(width - half - 2, Math.max(half + 2, X(i))), height - B + 8);
    }

    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    let drawing = false;
    series.forEach((v, i) => {
      if (v == null) {
        drawing = false;
        return;
      }
      if (drawing) ctx.lineTo(X(i), Y(v));
      else ctx.moveTo(X(i), Y(v));
      drawing = true;
    });
    ctx.stroke();

    // Keep markers from merging on a 90-day window drawn at phone width.
    const dot = Math.max(1.4, Math.min(3, (width - L - R) / span / 2.5));
    series.forEach((v, i) => {
      ctx.beginPath();
      if (v == null) {
        ctx.arc(X(i), Y(0) - 6, dot * 0.87, 0, Math.PI * 2);
        ctx.strokeStyle = "#9b968c";
        ctx.lineWidth = 1;
        ctx.stroke();
      } else {
        ctx.arc(X(i), Y(v), dot, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
      }
    });
  }, [history, days, color, width, height]);

  return (
    <canvas
      ref={canvasRef}
      className="canvas"
      // The border sits outside the drawing, which is exactly `height` tall.
      style={{ height: height + 2 }}
      role="img"
      aria-label={label}
    />
  );
}

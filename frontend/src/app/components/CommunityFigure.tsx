import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import type { Channel, Edge } from "../data/useAtlasData";
import { shortLabel, type AtlasIndex } from "../lib/atlas";
import { fmt } from "../lib/format";
import { isSmall } from "../lib/palette";

/**
 * The community map as a figure: dots on white, coloured by community, with
 * thin lines for shared chatters. Drawn in a fixed 1000 × 730 space and scaled
 * to whatever width the page gives it. Positions come from the pipeline; the
 * browser never runs a layout.
 */

const SPACE_W = 1000;
const SPACE_H = 730;
const PAD = 14;
const EDGE_ALPHA = 0.16;
const CROSS_EDGE = "#6f6a62";
const INK = "#1c1b19";

interface Node {
  id: string;
  communityId: string;
  x: number;
  y: number;
  r: number;
  name: string;
  viewers: number;
}

/**
 * frontend_exporter._node_radius, in layout units. The exporter separates
 * neighbouring nodes by these radii, so a dot drawn no larger than this never
 * overlaps another.
 */
function layoutRadius(viewers: number, maxViewers: number): number {
  if (maxViewers <= 0) return 4;
  return 4 + Math.sqrt(Math.max(0, viewers) / maxViewers) * 12;
}

/** Drawn radius in figure units: log-scaled, so the largest channels don't swamp their community. */
function dotRadius(viewers: number): number {
  return Math.max(1.6, 2.2 + 1.7 * Math.log10(Math.max(1, viewers) / 250));
}

/** Fit the precomputed layout into the figure, leaving room under it for labels. */
function placeNodes(channels: Channel[], scale: number): Node[] {
  if (!channels.length || !scale) return [];
  const maxViewers = Math.max(0, ...channels.map((c) => c.viewers));
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const c of channels) {
    const r = layoutRadius(c.viewers, maxViewers);
    minX = Math.min(minX, c.layout.x - r);
    maxX = Math.max(maxX, c.layout.x + r);
    minY = Math.min(minY, c.layout.y - r);
    maxY = Math.max(maxY, c.layout.y + r);
  }
  // Labels are drawn at a fixed screen size, so they take more figure units
  // on a narrow screen.
  const labelRoom = 22 / scale;
  const k = Math.min(
    (SPACE_W - 2 * PAD) / Math.max(1, maxX - minX),
    (SPACE_H - 2 * PAD - labelRoom) / Math.max(1, maxY - minY),
  );
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const midY = PAD + (SPACE_H - 2 * PAD - labelRoom) / 2;
  return channels.map((c) => ({
    id: c.id,
    communityId: c.communityId,
    x: SPACE_W / 2 + (c.layout.x - cx) * k,
    y: midY + (c.layout.y - cy) * k,
    r: Math.min(dotRadius(c.viewers), layoutRadius(c.viewers, maxViewers) * k),
    name: c.displayName,
    viewers: c.viewers,
  }));
}

interface PaintOptions {
  nodes: Node[];
  byId: Map<string, Node>;
  edges: Edge[];
  index: AtlasIndex;
  scale: number;
  hidden: ReadonlySet<string>;
  selected: string | null;
  hover: string | null;
  query: string;
}

function paint(canvas: HTMLCanvasElement, o: PaintOptions) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const s = o.scale;
  ctx.setTransform(dpr * s, 0, 0, dpr * s, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, SPACE_W, SPACE_H);

  const colorOf = (communityId: string) => o.index.color.get(communityId) ?? "#a8a39a";
  const shown = (n: Node) => !o.hidden.has(n.communityId);
  const q = o.query.trim().toLowerCase();
  const matches = (n: Node) => !q || n.name.toLowerCase().includes(q) || n.id.includes(q);

  ctx.lineCap = "round";
  for (const e of o.edges) {
    const a = o.byId.get(e.source);
    const b = o.byId.get(e.target);
    if (!a || !b || !shown(a) || !shown(b)) continue;
    const same = a.communityId === b.communityId;
    ctx.strokeStyle = same ? colorOf(a.communityId) : CROSS_EDGE;
    ctx.globalAlpha = (q ? 0.35 : 1) * (same ? EDGE_ALPHA : EDGE_ALPHA * 0.7);
    ctx.lineWidth = 0.5 + Math.log10(Math.max(1, e.weight)) * 0.45;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }

  const sel = o.selected ? o.byId.get(o.selected) : undefined;
  if (sel && shown(sel)) {
    ctx.strokeStyle = INK;
    ctx.globalAlpha = 0.55;
    for (const nb of o.index.neighbours.get(sel.id) ?? []) {
      const other = o.byId.get(nb.id);
      if (!other || !shown(other)) continue;
      ctx.lineWidth = 0.6 + Math.log10(Math.max(1, nb.weight)) * 0.55;
      ctx.beginPath();
      ctx.moveTo(sel.x, sel.y);
      ctx.lineTo(other.x, other.y);
      ctx.stroke();
    }
  }

  ctx.lineWidth = 0.9;
  ctx.strokeStyle = "#ffffff";
  for (const n of o.nodes) {
    if (!shown(n)) continue;
    ctx.globalAlpha = matches(n) ? 0.95 : 0.14;
    ctx.fillStyle = colorOf(n.communityId);
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }

  ctx.globalAlpha = 1;
  ctx.strokeStyle = INK;
  const ring = (n: Node | undefined, width: number, gap: number) => {
    if (!n || !shown(n)) return;
    ctx.lineWidth = width / s;
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.r + gap, 0, Math.PI * 2);
    ctx.stroke();
  };
  if (q) for (const n of o.nodes) if (matches(n)) ring(n, 1.2, 2.5);
  ring(sel, 1.8, 3);
  if (o.hover !== o.selected) ring(o.hover ? o.byId.get(o.hover) : undefined, 1.2, 2.5);

  // One label under each named community, largest first; a label that would
  // land on one already drawn is skipped rather than stacked.
  const regions = new Map<string, { x: number; n: number; bottom: number }>();
  for (const n of o.nodes) {
    const region = regions.get(n.communityId);
    if (region) {
      region.x += n.x;
      region.n += 1;
      region.bottom = Math.max(region.bottom, n.y + n.r);
    } else {
      regions.set(n.communityId, { x: n.x, n: 1, bottom: n.y + n.r });
    }
  }
  ctx.font = `${11.5 / s}px "IBM Plex Mono", monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.lineJoin = "round";
  const lineHeight = 15 / s;
  const placed: { x0: number; x1: number; y0: number; y1: number }[] = [];
  for (const community of o.index.communities) {
    const region = regions.get(community.id);
    if (!region || isSmall(community) || o.hidden.has(community.id)) continue;
    const text = shortLabel(community.label);
    const half = ctx.measureText(text).width / 2;
    const x = Math.min(SPACE_W - half - 4, Math.max(half + 4, region.x / region.n));
    const y = Math.min(SPACE_H - lineHeight - 2, region.bottom + 4 / s);
    const box = { x0: x - half, x1: x + half, y0: y, y1: y + lineHeight };
    if (placed.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0)) continue;
    placed.push(box);
    ctx.lineWidth = 4 / s;
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.strokeText(text, x, y);
    ctx.fillStyle = INK;
    ctx.fillText(text, x, y);
  }
}

interface CommunityFigureProps {
  channels: Channel[];
  edges: Edge[];
  index: AtlasIndex;
  /** What the figure shows, for screen readers. */
  label: string;
  /** Hover names a channel; clicking one calls onSelect. */
  interactive?: boolean;
  hidden?: ReadonlySet<string>;
  selected?: string | null;
  query?: string;
  onSelect?: (id: string) => void;
  /** Drawn over the figure, e.g. the not-ready notice. */
  children?: ReactNode;
}

const NONE: ReadonlySet<string> = new Set();

export function CommunityFigure({
  channels,
  edges,
  index,
  label,
  interactive = false,
  hidden = NONE,
  selected = null,
  query = "",
  onSelect,
  children,
}: CommunityFigureProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [fontsReady, setFontsReady] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ w: width, h: height });
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  // Labels use IBM Plex Mono; drawing before it arrives measures the fallback.
  useEffect(() => {
    let live = true;
    document.fonts?.ready.then(() => live && setFontsReady(true));
    return () => {
      live = false;
    };
  }, []);

  const scale = Math.min(size.w / SPACE_W, size.h / SPACE_H);
  const nodes = useMemo(() => placeNodes(channels, scale), [channels, scale]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const hoverId = interactive ? hover?.id ?? null : null;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !scale) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(size.w * dpr);
    const h = Math.round(size.h * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    paint(canvas, {
      nodes,
      byId,
      edges,
      index,
      scale,
      hidden,
      selected: interactive ? selected : null,
      hover: hoverId,
      query: interactive ? query : "",
    });
  }, [nodes, byId, edges, index, scale, size, hidden, selected, hoverId, query, interactive, fontsReady]);

  const hit = (e: MouseEvent<HTMLCanvasElement>): Node | null => {
    const canvas = e.currentTarget;
    const rect = canvas.getBoundingClientRect();
    if (!scale) return null;
    const mx = (e.clientX - rect.left - canvas.clientLeft) / scale;
    const my = (e.clientY - rect.top - canvas.clientTop) / scale;
    let best: Node | null = null;
    let bestDistance = Infinity;
    for (const n of nodes) {
      if (hidden.has(n.communityId)) continue;
      const d = Math.hypot(n.x - mx, n.y - my);
      if (d < n.r + 4 && d < bestDistance) {
        best = n;
        bestDistance = d;
      }
    }
    return best;
  };

  const onMove = (e: MouseEvent<HTMLCanvasElement>) => {
    const node = hit(e);
    if (!node) {
      if (hover) setHover(null);
      return;
    }
    if (node.id === hover?.id) return;
    const frame = e.currentTarget.parentElement!.getBoundingClientRect();
    setHover({ id: node.id, x: e.clientX - frame.left, y: e.clientY - frame.top });
  };

  const hovered = hoverId ? byId.get(hoverId) : undefined;
  const hoveredCommunity = hovered ? index.community.get(hovered.communityId) : undefined;
  // On the right half the label would run past the figure; flip it to the left.
  const flip = hover && size.w ? hover.x > size.w * 0.5 : false;

  return (
    <div className={`map-frame${children ? " map-frame--notice" : ""}`}>
      <canvas
        ref={canvasRef}
        className="canvas canvas--map"
        role="img"
        aria-label={label}
        style={{ cursor: hovered ? "pointer" : "default" }}
        onMouseMove={interactive ? onMove : undefined}
        onMouseLeave={interactive ? () => setHover(null) : undefined}
        onClick={
          interactive
            ? (e) => {
                const node = hit(e);
                if (node) onSelect?.(node.id);
              }
            : undefined
        }
      />
      {hovered && hover && (
        <div
          className="map-tip"
          style={{
            left: hover.x,
            top: hover.y,
            transform: flip ? "translate(calc(-100% - 10px), -50%)" : "translate(10px, -50%)",
          }}
        >
          {hovered.name} ·{" "}
          {hoveredCommunity && !isSmall(hoveredCommunity)
            ? shortLabel(hoveredCommunity.label)
            : "small community"}{" "}
          · {fmt(hovered.viewers)} viewers
        </div>
      )}
      {children}
    </div>
  );
}

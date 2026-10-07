import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import type { Channel, Edge, Community } from "../data/useAtlasData";
import { hexToRgb } from "../lib/color";

interface NodeState {
  id: string;
  communityId: string;
  x: number;
  y: number;
  r: number;
  color: string;
  label: string;
  viewers: number;
}

interface NetworkGraphProps {
  channels: Channel[];
  edges: Edge[];
  communities: Community[];
  className?: string;
  onNodeClick?: (channelId: string) => void;
  highlightedNode?: string | null;
}

// Pointer travel, in screen pixels, past which a press-and-release is a pan.
const DRAG_CLICK_SLOP = 4;

export function NetworkGraph({
  channels,
  edges,
  communities,
  className = "",
  onNodeClick,
  highlightedNode,
}: NetworkGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Whether the pointer travelled far enough between press and release to be a
  // pan rather than a click. Selecting is a toggle, so releasing over a node at
  // the end of a drag must not count as clicking it — that would clear the
  // selection the user was panning around to look at.
  const dragMovedRef = useRef(false);
  const nodesRef = useRef<NodeState[]>([]);
  const transformRef = useRef({ x: 0, y: 0, scale: 1 });
  const animFrameRef = useRef<number>(0);
  const isDraggingRef = useRef(false);
  const lastMouseRef = useRef({ x: 0, y: 0 });
  const hoveredNodeRef = useRef<string | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; node: NodeState } | null>(null);
  const needsFitRef = useRef(true);
  const communityNameMap = useMemo(
    () => new Map(communities.map((community) => [community.id, community.label])),
    [communities],
  );
  const communityColorMap = useMemo(
    () => new Map(communities.map((community) => [community.id, community.color])),
    [communities],
  );

  useEffect(() => {
    const maxViewers = Math.max(1, ...channels.map((c) => c.viewers));
    // Sized for the ~1000-node graph the pipeline publishes.
    const minR = 4;
    const maxR = 16;

    nodesRef.current = channels.map((ch) => ({
      id: ch.id,
      communityId: ch.communityId,
      x: ch.layout.x,
      y: ch.layout.y,
      r: minR + ((ch.viewers / maxViewers) ** 0.5) * (maxR - minR),
      color: communityColorMap.get(ch.communityId) || "#9147FF",
      label: ch.displayName,
      viewers: ch.viewers,
    }));
    needsFitRef.current = true;
  }, [channels, communityColorMap]);

  // Frame the graph on load. Without this the view sits at scale 1 around the
  // origin, so whether the graph fills the canvas depends on the coordinate
  // scale the pipeline happened to export.
  const fitToContent = useCallback(() => {
    const canvas = canvasRef.current;
    const nodes = nodesRef.current;
    if (!canvas || !nodes.length || !canvas.width || !canvas.height) return;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of nodes) {
      minX = Math.min(minX, n.x - n.r); maxX = Math.max(maxX, n.x + n.r);
      minY = Math.min(minY, n.y - n.r); maxY = Math.max(maxY, n.y + n.r);
    }
    const spanX = Math.max(1, maxX - minX);
    const spanY = Math.max(1, maxY - minY);
    const scale = Math.min(4, Math.max(0.3, Math.min(
      (canvas.width * 0.92) / spanX,
      (canvas.height * 0.92) / spanY,
    )));
    transformRef.current = {
      scale,
      x: -((minX + maxX) / 2) * scale,
      y: -((minY + maxY) / 2) * scale,
    };
    needsFitRef.current = false;
  }, []);

  const drawGraph = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { width, height } = canvas;
    const { x: tx, y: ty, scale } = transformRef.current;
    const nodes = nodesRef.current;
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));

    ctx.clearRect(0, 0, width, height);

    // Background
    ctx.fillStyle = "#0E0E10";
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.translate(width / 2 + tx, height / 2 + ty);
    ctx.scale(scale, scale);

    const maxWeight = Math.max(1, ...edges.map((e) => e.weight));

    // Draw edges
    for (const edge of edges) {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (!src || !tgt) continue;

      const isHighlighted =
        highlightedNode &&
        (edge.source === highlightedNode || edge.target === highlightedNode);

      const normalizedWeight = edge.weight / maxWeight;
      const lineWidth = 0.5 + normalizedWeight * 3.5;
      const alpha = highlightedNode
        ? isHighlighted ? 0.8 : 0.08
        : 0.2 + normalizedWeight * 0.35;

      const srcColor = hexToRgb(src.color);

      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.strokeStyle = `rgba(${srcColor.r}, ${srcColor.g}, ${srcColor.b}, ${alpha})`;
      ctx.lineWidth = lineWidth / scale;
      ctx.stroke();
    }

    // Draw nodes
    for (const node of nodes) {
      const isHighlighted = node.id === highlightedNode;
      const isHovered = node.id === hoveredNodeRef.current;
      const isDimmed = highlightedNode && !isHighlighted;

      const rgb = hexToRgb(node.color);
      const nodeAlpha = isDimmed ? 0.3 : 1;
      const r = node.r * (isHovered ? 1.25 : 1);

      // Glow
      if (isHighlighted || isHovered) {
        const glow = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, r * 3);
        glow.addColorStop(0, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`);
        glow.addColorStop(1, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0)`);
        ctx.beginPath();
        ctx.arc(node.x, node.y, r * 3, 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();
      }

      // Node circle
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${nodeAlpha})`;
      ctx.fill();

      // Border
      ctx.strokeStyle = `rgba(255, 255, 255, ${isDimmed ? 0.1 : 0.3})`;
      ctx.lineWidth = 1 / scale;
      ctx.stroke();

      // Label for large nodes or highlighted
      if (node.r > 12 || isHighlighted || isHovered) {
        const fontSize = Math.max(8, Math.min(13, node.r * 0.9));
        ctx.font = `${fontSize / scale}px "Space Grotesk", sans-serif`;
        ctx.fillStyle = `rgba(239, 239, 241, ${isDimmed ? 0.3 : 0.9})`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(node.label, node.x, node.y + r + (fontSize * 0.9) / scale);
      }
    }

    // Community region labels. With the two-level layout each community is its
    // own cluster, so one label per region reads far better than per-node
    // labels — which at this node count only ever landed on a few hubs.
    if (!highlightedNode) {
      const groups = new Map<string, { x: number; y: number; n: number; maxY: number }>();
      for (const node of nodes) {
        if (!node.communityId) continue;
        const g = groups.get(node.communityId);
        if (g) {
          g.x += node.x; g.y += node.y; g.n += 1;
          g.maxY = Math.max(g.maxY, node.y + node.r);
        } else {
          groups.set(node.communityId, { x: node.x, y: node.y, n: 1, maxY: node.y + node.r });
        }
      }
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.font = `600 ${13 / scale}px "Space Grotesk", sans-serif`;
      const lineHeight = 15 / scale;
      // Largest regions get first claim on space; a smaller region's label is
      // dropped rather than stacked on top of one already drawn.
      const placed: { x0: number; x1: number; y0: number; y1: number }[] = [];
      const ordered = [...groups.entries()].sort((a, b) => b[1].n - a[1].n);
      for (const [id, g] of ordered) {
        if (g.n < 12) continue;
        const name = communityNameMap.get(id);
        if (!name) continue;
        const cx = g.x / g.n;
        const cy = g.maxY + 10 / scale;
        const halfWidth = ctx.measureText(name).width / 2;
        const box = { x0: cx - halfWidth, x1: cx + halfWidth, y0: cy, y1: cy + lineHeight };
        if (placed.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0)) {
          continue;
        }
        placed.push(box);
        ctx.fillStyle = "rgba(239, 239, 241, 0.92)";
        ctx.strokeStyle = "rgba(10, 10, 15, 0.85)";
        ctx.lineWidth = 3 / scale;
        ctx.strokeText(name, cx, cy);
        ctx.fillText(name, cx, cy);
      }
    }

    ctx.restore();
  }, [edges, highlightedNode, communityNameMap]);

  // Animation loop
  useEffect(() => {
    let running = true;

    const loop = () => {
      if (!running) return;

      // Refit once the canvas has real dimensions, and again after a resize.
      if (needsFitRef.current) fitToContent();

      drawGraph();
      animFrameRef.current = requestAnimationFrame(loop);
    };

    animFrameRef.current = requestAnimationFrame(loop);
    return () => {
      running = false;
      cancelAnimationFrame(animFrameRef.current);
    };
  }, [drawGraph, fitToContent]);

  // Resize observer
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ro = new ResizeObserver(() => {
      const parent = canvas.parentElement;
      if (!parent) return;
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      needsFitRef.current = true;
    });
    ro.observe(canvas.parentElement!);
    return () => ro.disconnect();
  }, []);

  // Mouse events
  const getWorldPos = useCallback((canvasX: number, canvasY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const { x: tx, y: ty, scale } = transformRef.current;
    return {
      x: (canvasX - canvas.width / 2 - tx) / scale,
      y: (canvasY - canvas.height / 2 - ty) / scale,
    };
  }, []);

  const findNodeAtPos = useCallback((wx: number, wy: number): NodeState | null => {
    for (const node of nodesRef.current) {
      const dx = node.x - wx;
      const dy = node.y - wy;
      if (Math.sqrt(dx * dx + dy * dy) <= node.r + 4) return node;
    }
    return null;
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;

    if (isDraggingRef.current) {
      const dx = cx - lastMouseRef.current.x;
      const dy = cy - lastMouseRef.current.y;
      // A few pixels of travel is the hand shaking on a click, not a pan.
      if (Math.abs(dx) + Math.abs(dy) > DRAG_CLICK_SLOP) dragMovedRef.current = true;
      transformRef.current.x += dx;
      transformRef.current.y += dy;
      lastMouseRef.current = { x: cx, y: cy };
      return;
    }

    const { x: wx, y: wy } = getWorldPos(cx, cy);
    const node = findNodeAtPos(wx, wy);
    hoveredNodeRef.current = node?.id ?? null;

    if (node) {
      setTooltip({
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
        node,
      });
      canvasRef.current!.style.cursor = "pointer";
    } else {
      setTooltip(null);
      canvasRef.current!.style.cursor = "grab";
    }
  }, [getWorldPos, findNodeAtPos]);

  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    isDraggingRef.current = true;
    dragMovedRef.current = false;
    const rect = canvasRef.current!.getBoundingClientRect();
    lastMouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    canvasRef.current!.style.cursor = "grabbing";
  }, []);

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    isDraggingRef.current = false;
    const rect = canvasRef.current!.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const { x: wx, y: wy } = getWorldPos(cx, cy);
    const node = findNodeAtPos(wx, wy);
    if (node && onNodeClick && !dragMovedRef.current) onNodeClick(node.id);
    canvasRef.current!.style.cursor = node ? "pointer" : "grab";
  }, [getWorldPos, findNodeAtPos, onNodeClick]);

  // React registers wheel listeners as passive, so preventDefault inside an
  // onWheel prop is ignored and zooming the map also scrolls the page.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const factor = e.deltaY > 0 ? 0.9 : 1.1;
      transformRef.current.scale = Math.min(4, Math.max(0.3, transformRef.current.scale * factor));
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);

  const handleMouseLeave = useCallback(() => {
    isDraggingRef.current = false;
    hoveredNodeRef.current = null;
    setTooltip(null);
  }, []);

  return (
    <div className={`relative overflow-hidden ${className}`} style={{ background: "#0E0E10" }}>
      <canvas
        ref={canvasRef}
        className="block w-full h-full"
        style={{ cursor: "grab" }}
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
      />
      {tooltip && (
        <div
          className="pointer-events-none absolute z-20 px-3 py-2 rounded-lg text-sm shadow-xl"
          style={{
            left: tooltip.x + 16,
            top: tooltip.y - 10,
            background: "#18181B",
            border: "1px solid #2A2A2E",
            color: "#EFEFF1",
            maxWidth: 200,
          }}
        >
          <div style={{ color: tooltip.node.color }} className="font-semibold mb-0.5">
            {tooltip.node.label}
          </div>
          <div style={{ color: "#848494", fontSize: 12 }}>
            {tooltip.node.viewers.toLocaleString()} viewers
          </div>
          <div style={{ color: "#848494", fontSize: 11, marginTop: 2 }}>
            Click to view details
          </div>
        </div>
      )}
    </div>
  );
}

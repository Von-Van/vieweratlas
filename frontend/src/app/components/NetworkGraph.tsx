import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import type { Channel, Edge, Community } from "../data/mockData";

interface NodeState {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
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
  interactive?: boolean;
  onNodeClick?: (channelId: string) => void;
  highlightedNode?: string | null;
}

function seededRandom(seed: number) {
  let s = seed;
  return () => {
    s = (Math.imul(1664525, s) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function hexToRgb(hex: string) {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return result
    ? { r: parseInt(result[1], 16), g: parseInt(result[2], 16), b: parseInt(result[3], 16) }
    : { r: 145, g: 71, b: 255 };
}

function hashString(value: string) {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = Math.imul(31, hash) + value.charCodeAt(i);
  }
  return Math.abs(hash);
}

export function NetworkGraph({
  channels,
  edges,
  communities,
  className = "",
  interactive = true,
  onNodeClick,
  highlightedNode,
}: NetworkGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<NodeState[]>([]);
  const transformRef = useRef({ x: 0, y: 0, scale: 1 });
  const animFrameRef = useRef<number>(0);
  const isDraggingRef = useRef(false);
  const lastMouseRef = useRef({ x: 0, y: 0 });
  const hoveredNodeRef = useRef<string | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; node: NodeState } | null>(null);
  const tickRef = useRef(0);
  const shouldSimulateRef = useRef(true);
  const communityColorMap = useMemo(
    () => new Map(communities.map((community) => [community.id, community.color])),
    [communities],
  );
  const communityOrder = useMemo(
    () => communities.map((community) => community.id),
    [communities],
  );

  // Build nodes from channels
  useEffect(() => {
    if (channels.length === 0) {
      nodesRef.current = [];
      tickRef.current = 0;
      shouldSimulateRef.current = false;
      return;
    }

    const rng = seededRandom(42);
    const maxViewers = Math.max(1, ...channels.map((c) => c.viewers));
    const minR = 6;
    const maxR = 26;
    const orderLength = Math.max(communityOrder.length, 1);
    const hasPrecomputedLayout = channels.every((ch) => ch.layout);

    nodesRef.current = channels.map((ch) => {
      const knownCommunityIndex = communityOrder.indexOf(ch.communityId);
      const commIdx = knownCommunityIndex >= 0
        ? knownCommunityIndex
        : hashString(ch.communityId) % orderLength;
      const commAngle = (commIdx / orderLength) * Math.PI * 2;
      const radius = 180 + rng() * 60;
      const spread = 0.4 + rng() * 0.4;
      const angle = commAngle + (rng() - 0.5) * spread * 2;

      const r = minR + ((ch.viewers / maxViewers) ** 0.5) * (maxR - minR);
      const color = communityColorMap.get(ch.communityId) || "#9147FF";

      return {
        id: ch.id,
        x: ch.layout?.x ?? Math.cos(angle) * radius,
        y: ch.layout?.y ?? Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        r,
        color,
        label: ch.displayName,
        viewers: ch.viewers,
      };
    });

    tickRef.current = 0;
    shouldSimulateRef.current = !hasPrecomputedLayout && channels.length <= 350;
  }, [channels, communityColorMap, communityOrder]);

  const runSimulationStep = useCallback((alpha: number) => {
    const nodes = nodesRef.current;
    const repulsion = 800;
    const attraction = 0.04;
    const gravity = 0.008;

    // Repulsion between all pairs
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const distSq = dx * dx + dy * dy || 0.01;
        const dist = Math.sqrt(distSq);
        const force = (repulsion / distSq) * alpha;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        nodes[i].vx -= fx;
        nodes[i].vy -= fy;
        nodes[j].vx += fx;
        nodes[j].vy += fy;
      }
    }

    // Attraction along edges
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    for (const edge of edges) {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (!src || !tgt) continue;
      const dx = tgt.x - src.x;
      const dy = tgt.y - src.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const force = dist * attraction * alpha;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      src.vx += fx;
      src.vy += fy;
      tgt.vx -= fx;
      tgt.vy -= fy;
    }

    // Center gravity
    for (const node of nodes) {
      node.vx -= node.x * gravity * alpha;
      node.vy -= node.y * gravity * alpha;
    }

    // Apply velocity with damping
    for (const node of nodes) {
      node.x += node.vx;
      node.y += node.vy;
      node.vx *= 0.85;
      node.vy *= 0.85;
    }
  }, [edges]);

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

    ctx.restore();
  }, [edges, highlightedNode]);

  // Animation loop
  useEffect(() => {
    let running = true;

    const loop = () => {
      if (!running) return;

      if (shouldSimulateRef.current && tickRef.current < 300) {
        const alpha = Math.max(0.01, 1 - tickRef.current / 250);
        for (let i = 0; i < 3; i++) runSimulationStep(alpha);
        tickRef.current += 3;
      }

      drawGraph();
      animFrameRef.current = requestAnimationFrame(loop);
    };

    animFrameRef.current = requestAnimationFrame(loop);
    return () => {
      running = false;
      cancelAnimationFrame(animFrameRef.current);
    };
  }, [drawGraph, runSimulationStep]);

  // Resize observer
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ro = new ResizeObserver(() => {
      const parent = canvas.parentElement;
      if (!parent) return;
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
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
    if (!interactive) return;
    const rect = canvasRef.current!.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;

    if (isDraggingRef.current) {
      transformRef.current.x += cx - lastMouseRef.current.x;
      transformRef.current.y += cy - lastMouseRef.current.y;
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
  }, [interactive, getWorldPos, findNodeAtPos]);

  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!interactive) return;
    isDraggingRef.current = true;
    const rect = canvasRef.current!.getBoundingClientRect();
    lastMouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    canvasRef.current!.style.cursor = "grabbing";
  }, [interactive]);

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!interactive) return;
    isDraggingRef.current = false;
    const rect = canvasRef.current!.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const { x: wx, y: wy } = getWorldPos(cx, cy);
    const node = findNodeAtPos(wx, wy);
    if (node && onNodeClick) onNodeClick(node.id);
    canvasRef.current!.style.cursor = node ? "pointer" : "grab";
  }, [interactive, getWorldPos, findNodeAtPos, onNodeClick]);

  const handleWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    if (!interactive) return;
    e.preventDefault();
    const factor = e.deltaY > 0 ? 0.9 : 1.1;
    transformRef.current.scale = Math.min(4, Math.max(0.3, transformRef.current.scale * factor));
  }, [interactive]);

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
        style={{ cursor: interactive ? "grab" : "default" }}
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onWheel={handleWheel}
      />
      {tooltip && interactive && (
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

import type { AtlasData, Channel, Community } from "../data/useAtlasData";
import { displayColors, isSmall } from "./palette";

export interface Neighbour {
  id: string;
  weight: number;
}

/** Lookups every page needs, derived once per loaded payload. */
export interface AtlasIndex {
  channel: Map<string, Channel>;
  community: Map<string, Community>;
  /** Communities, largest first. */
  communities: Community[];
  /** Display colour per community id. */
  color: Map<string, string>;
  /** Rendered neighbours per channel, most shared chatters first. */
  neighbours: Map<string, Neighbour[]>;
  /** Shared chatters summed over a channel's rendered links. */
  sharedTotal: Map<string, number>;
  /** Rendered links that stay inside the channel's own community. */
  inside: Map<string, number>;
  /** Members per community id, in payload order (most viewers first). */
  members: Map<string, Channel[]>;
  /** Most links first; ties broken by shared chatters across those links. */
  mostConnected: Channel[];
  /** Weakest rendered link. Every line on the map has at least this many shared chatters. */
  minWeight: number;
}

const cache = new WeakMap<AtlasData, AtlasIndex>();

export function atlasIndex(data: AtlasData): AtlasIndex {
  const hit = cache.get(data);
  if (hit) return hit;

  const channel = new Map(data.channels.map((c) => [c.id, c]));
  const community = new Map(data.communities.map((c) => [c.id, c]));
  const communities = [...data.communities].sort(
    (a, b) => b.nodeCount - a.nodeCount || a.label.localeCompare(b.label),
  );

  const neighbours = new Map<string, Neighbour[]>(data.channels.map((c) => [c.id, []]));
  let minWeight = Infinity;
  for (const edge of data.edges) {
    neighbours.get(edge.source)?.push({ id: edge.target, weight: edge.weight });
    neighbours.get(edge.target)?.push({ id: edge.source, weight: edge.weight });
    minWeight = Math.min(minWeight, edge.weight);
  }

  const sharedTotal = new Map<string, number>();
  const inside = new Map<string, number>();
  for (const [id, list] of neighbours) {
    list.sort((a, b) => b.weight - a.weight || a.id.localeCompare(b.id));
    const own = channel.get(id)?.communityId;
    sharedTotal.set(id, list.reduce((sum, n) => sum + n.weight, 0));
    inside.set(id, list.filter((n) => channel.get(n.id)?.communityId === own).length);
  }

  const members = new Map<string, Channel[]>();
  for (const c of data.channels) {
    const list = members.get(c.communityId);
    if (list) list.push(c);
    else members.set(c.communityId, [c]);
  }

  const degree = (id: string) => neighbours.get(id)?.length ?? 0;
  const mostConnected = [...data.channels].sort(
    (a, b) =>
      degree(b.id) - degree(a.id) ||
      (sharedTotal.get(b.id) ?? 0) - (sharedTotal.get(a.id) ?? 0) ||
      a.id.localeCompare(b.id),
  );

  const index: AtlasIndex = {
    channel,
    community,
    communities,
    color: displayColors(data.communities),
    neighbours,
    sharedTotal,
    inside,
    members,
    mostConnected,
    minWeight: Number.isFinite(minWeight) ? minWeight : 0,
  };
  cache.set(data, index);
  return index;
}

/** Community name for a table cell: its label, or "small community". */
export function communityName(community: Community | undefined): string {
  if (!community) return "unknown";
  return isSmall(community) ? "small community" : community.label;
}

/**
 * Short form for labels drawn on the map:
 * "Variety (en) · Kaicenat, Jynxzi" → "en · Kaicenat, Jynxzi",
 * "Just Chatting (en)" → "Just Chatting". A long pair keeps its first name.
 */
export function shortLabel(label: string): string {
  let short = label;
  const variety = /^Variety \(([^)]+)\) · (.+)$/.exec(label);
  const suffixed = /^(.+?) \(([a-z]{2,3}(?:-[a-z0-9]+)?)\)$/i.exec(label);
  if (variety) short = `${variety[1]} · ${variety[2]}`;
  else if (suffixed && !label.startsWith("Variety")) short = suffixed[1];
  if (short.length > 24 && short.includes(", ")) short = short.slice(0, short.indexOf(", "));
  return short;
}

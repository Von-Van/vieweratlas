import type { Community } from "../data/useAtlasData";

/**
 * frontend_exporter.COMMUNITY_COLORS, in the order the exporter hands them out
 * (by community size rank). A community carries its colour from window to
 * window, so the colour in the payload is its identity rather than a choice
 * this site has to keep.
 */
const EXPORTER_COLORS = [
  "#9147ff", "#00e5cc", "#ff7b00", "#1db954", "#ff4d6d", "#ffd700",
  "#4299e1", "#e53e3e", "#38b2ac", "#d69e2e", "#9f7aea", "#ed64a6",
];

/**
 * What the site draws instead: matplotlib's tab10 without its grey (grey means
 * "small community" here), then tab20b for communities past the ninth. The
 * exporter's palette is shorter, so its later ranks repeat colours; the extra
 * entries let every large community on the map get its own.
 */
export const PALETTE = [
  "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
  "#e377c2", "#bcbd22", "#17becf", "#393b79", "#637939", "#8c6d31",
  "#843c39", "#7b4173", "#5254a3", "#8ca252", "#bd9e39", "#ad494a",
  "#a55194",
];

/** Colour for communities too small on the map to name. */
export const SMALL_COLOR = "#a8a39a";

/**
 * Communities with fewer channels than this *on the map* are drawn grey and
 * grouped in the legend. Each has at least min_community_size (10) channels
 * in the full graph; the public cap is what shrank them.
 */
export const SMALL_COMMUNITY = 10;

export function isSmall(community: Community): boolean {
  return community.nodeCount < SMALL_COMMUNITY;
}

/**
 * Display colour per community id. Largest first, so when two communities
 * share an exporter colour the larger one keeps the matching palette entry
 * and the other takes the next free one.
 */
export function displayColors(communities: Community[]): Map<string, string> {
  const out = new Map<string, string>();
  const used = new Set<string>();
  const bySize = [...communities].sort((a, b) => b.nodeCount - a.nodeCount);

  for (const community of bySize) {
    if (isSmall(community)) {
      out.set(community.id, SMALL_COLOR);
      continue;
    }
    const raw = community.color.toLowerCase();
    let color: string | undefined = PALETTE.includes(raw)
      ? raw
      : PALETTE[EXPORTER_COLORS.indexOf(raw)];
    if (!color || used.has(color)) {
      color = PALETTE.find((candidate) => !used.has(candidate)) ?? PALETTE[used.size % PALETTE.length];
    }
    used.add(color);
    out.set(community.id, color);
  }
  return out;
}

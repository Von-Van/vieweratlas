import type { AtlasData } from "./useAtlasData";

const MAX_COMMUNITIES = 500;
const MAX_CHANNELS = 20_000;
const MAX_EDGES = 250_000;
const CHANNEL_ID = /^[a-z0-9_]{1,25}$/i;
const COMMUNITY_ID = /^[a-z0-9][a-z0-9-]{0,79}$/i;
const HEX_COLOR = /^#[0-9a-f]{6}$/i;

type JsonObject = Record<string, unknown>;

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown, maxLength = 500): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maxLength;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isCommunity(value: unknown): boolean {
  return (
    isObject(value) &&
    isString(value.id, 80) &&
    COMMUNITY_ID.test(value.id) &&
    isString(value.label, 120) &&
    isString(value.color, 7) &&
    HEX_COLOR.test(value.color) &&
    isCount(value.nodeCount) &&
    isString(value.description, 500)
  );
}

function isChannel(value: unknown): boolean {
  if (!isObject(value)) return false;
  if (
    !isString(value.id, 25) ||
    !CHANNEL_ID.test(value.id) ||
    !isString(value.name, 25) ||
    !CHANNEL_ID.test(value.name) ||
    !isString(value.displayName, 80) ||
    !isString(value.game, 120) ||
    !isCount(value.viewers) ||
    !isString(value.communityId, 80) ||
    !COMMUNITY_ID.test(value.communityId) ||
    !isString(value.description, 500) ||
    !isString(value.language, 80) ||
    !isCount(value.edgeCount) ||
    !isFiniteNumber(value.modularityScore)
  ) {
    return false;
  }

  if (!Array.isArray(value.topOverlaps) || value.topOverlaps.length > 20) return false;
  if (
    !value.topOverlaps.every(
      (overlap) =>
        isObject(overlap) &&
        isString(overlap.channelId, 25) &&
        CHANNEL_ID.test(overlap.channelId) &&
        isString(overlap.channelName, 80) &&
        isCount(overlap.shared),
    )
  ) {
    return false;
  }

  return (
    Array.isArray(value.viewerHistory) &&
    value.viewerHistory.length <= 1_000 &&
    value.viewerHistory.every(
      (point) =>
        isObject(point) &&
        isString(point.date, 80) &&
        isCount(point.viewers),
    )
  );
}

function isOverallStats(value: unknown): boolean {
  return (
    isObject(value) &&
    isCount(value.totalChannels) &&
    isCount(value.totalViewers) &&
    isCount(value.communitiesDetected) &&
    isFiniteNumber(value.modularityScore) &&
    isString(value.collectionPeriod, 160) &&
    isCount(value.dataPoints) &&
    isCount(value.edgesTotal) &&
    isFiniteNumber(value.avgOverlapWeight)
  );
}

export function validateAtlasData(value: unknown): AtlasData {
  if (!isObject(value)) throw new Error("Atlas data must be a JSON object");

  const { communities, channels, edges, overallStats, topCommunitiesBySize, mostConnectedChannels } = value;

  if (!Array.isArray(communities) || communities.length > MAX_COMMUNITIES || !communities.every(isCommunity)) {
    throw new Error("Atlas communities are invalid");
  }
  if (!Array.isArray(channels) || channels.length > MAX_CHANNELS || !channels.every(isChannel)) {
    throw new Error("Atlas channels are invalid");
  }

  const channelIds = new Set(channels.map((channel) => (channel as JsonObject).id));
  const communityIds = new Set(communities.map((community) => (community as JsonObject).id));
  if (!channels.every((channel) => communityIds.has((channel as JsonObject).communityId))) {
    throw new Error("Atlas channel references an unknown community");
  }

  if (
    !Array.isArray(edges) ||
    edges.length > MAX_EDGES ||
    !edges.every(
      (edge) =>
        isObject(edge) &&
        isString(edge.source, 25) &&
        isString(edge.target, 25) &&
        channelIds.has(edge.source) &&
        channelIds.has(edge.target) &&
        isCount(edge.weight),
    )
  ) {
    throw new Error("Atlas edges are invalid");
  }

  if (!isOverallStats(overallStats)) throw new Error("Atlas summary statistics are invalid");

  if (
    !Array.isArray(topCommunitiesBySize) ||
    topCommunitiesBySize.length > MAX_COMMUNITIES ||
    !topCommunitiesBySize.every(
      (community) =>
        isObject(community) &&
        isString(community.community, 120) &&
        isCount(community.channels) &&
        isCount(community.viewers),
    )
  ) {
    throw new Error("Atlas community rankings are invalid");
  }

  if (
    !Array.isArray(mostConnectedChannels) ||
    mostConnectedChannels.length > 100 ||
    !mostConnectedChannels.every(
      (channel) =>
        isObject(channel) &&
        isString(channel.name, 80) &&
        isCount(channel.edges) &&
        isString(channel.community, 120) &&
        isString(channel.color, 7) &&
        HEX_COLOR.test(channel.color),
    )
  ) {
    throw new Error("Atlas connected-channel rankings are invalid");
  }

  return value as unknown as AtlasData;
}

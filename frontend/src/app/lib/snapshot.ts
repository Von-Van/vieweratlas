/**
 * Findings from one fixed run: the 14-day sample in README.md ("Example
 * Results"). 42 surveys, 13–26 Aug 2026, regenerated 22 Sep 2026 at an overlap
 * threshold of 3, before the thresholds were re-swept on 1 Oct 2026.
 *
 * The public payload carries none of these: the bot-filter report, the
 * one-channel share and the tagging split live only in the private run record,
 * and the early funnel stages aren't recorded at all. So they are quoted here
 * and labelled as a snapshot wherever they appear, and they do not follow the
 * window picker. Keep them in step with the README.
 */
export const SNAPSHOT = {
  label: "14-day run, 13–26 Aug 2026",
  surveys: 42,
  threshold: 3,
  channelSamples: 47_864,
  channelsSampled: 11_835,
  channelsEligible: 5_082,
  graphChannels: 3_246,
  graphEdges: 13_055,
  communityChannels: 2_748,
  communities: 36,
  modularity: 0.81,
  mapChannels: 900,
  mapEdges: 4_294,
  mapCommunities: 19,
  chatters: 897_865,
  bots: {
    accounts: 229,
    shareOfChatters: "0.03%",
    shareOfOverlap: 97,
    edgesAdded: 681,
    shareOfUnfilteredEdges: 5,
  },
  oneChannelShare: 86,
  /** Rounded in the README too. */
  multiChannelChatters: 122_000,
  gameCommunities: 13,
  languageCommunities: 23,
} as const;

/** Production overlap thresholds (config.get_rigorous_config), as of the 1 Oct 2026 re-sweep. */
export const THRESHOLDS = [
  { window: 14, value: "4", status: "re-measured 1 Oct 2026, with the bot filter" },
  { window: 30, value: "5", status: "re-measured 1 Oct 2026, with the bot filter" },
  { window: 90, value: "5", status: "placeholder, measured on 50 days. Re-measure once the window fills" },
] as const;

/** The exporter's per-channel cap (frontend_top_edges_per_channel). */
export const LINK_CAP = 25;

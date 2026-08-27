import { createContext, useContext } from "react";
import type { Community, Channel, Edge } from "./mockData";

export interface OverallStats {
  totalChannels: number;
  totalViewers: number;
  communitiesDetected: number;
  modularityScore: number;
  collectionPeriod: string;
  dataPoints: number;
  edgesTotal: number;
  avgOverlapWeight: number;
}

export interface TopCommunity {
  community: string;
  channels: number;
  viewers: number;
}

export interface ConnectedChannel {
  name: string;
  edges: number;
  community: string;
  color: string;
}

export interface AtlasData {
  /**
   * Windows the pipeline actually published alongside this file. Absent for a
   * single-window export, which is the signal to hide the time filter.
   */
  availableWindows?: number[];
  /** Configured windows without enough survey history yet. Shown as PENDING. */
  pendingWindows?: number[];
  /** Which window the site opens on. */
  defaultWindow?: number;
  communities: Community[];
  channels: Channel[];
  edges: Edge[];
  overallStats: OverallStats;
  topCommunitiesBySize: TopCommunity[];
  mostConnectedChannels: ConnectedChannel[];
}

/** Rolling windows the pipeline publishes, in `analysis_windows` order. */
export const ANALYSIS_WINDOWS = [14, 30, 90] as const;

export type AnalysisWindow = (typeof ANALYSIS_WINDOWS)[number];

/** The canonical window — the one the pipeline also writes unsuffixed. */
export const DEFAULT_WINDOW: AnalysisWindow = 30;

export interface AtlasDataState {
  data: AtlasData | null;
  loading: boolean;
  error: string | null;
  source: "loading" | "live" | "demo";
  notice: string | null;
  /** Active rolling window. Global, so every page describes the same dataset. */
  window: AnalysisWindow;
  setWindow: (days: AnalysisWindow) => void;
  /**
   * False when only one window can be served — the bundled demo dataset, or a
   * VITE_DATA_URL the sibling keys cannot be derived from. The control hides
   * rather than offering windows that would 404.
   */
  windowAvailable: boolean;
  /** Windows this deployment can actually serve, in ascending order. */
  availableWindows: AnalysisWindow[];
  /**
   * Windows the filter offers but cannot load yet, because the surveys do not
   * reach back far enough. Selecting one shows PENDING instead of a graph.
   */
  pendingWindows: AnalysisWindow[];
  /** True when the selected window has no data behind it yet. */
  windowPending: boolean;
  /** A window switch is in flight; the previous window stays on screen. */
  switching: boolean;
}

export const AtlasDataContext = createContext<AtlasDataState>({
  data: null,
  loading: true,
  error: null,
  source: "loading",
  notice: null,
  window: DEFAULT_WINDOW,
  setWindow: () => {},
  windowAvailable: false,
  availableWindows: [],
  pendingWindows: [],
  windowPending: false,
  switching: false,
});

export function useAtlasData(): AtlasDataState {
  return useContext(AtlasDataContext);
}

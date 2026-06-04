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
  communities: Community[];
  channels: Channel[];
  edges: Edge[];
  overallStats: OverallStats;
  topCommunitiesBySize: TopCommunity[];
  mostConnectedChannels: ConnectedChannel[];
}

export interface AtlasDataState {
  data: AtlasData | null;
  loading: boolean;
  error: string | null;
  source: "loading" | "live" | "demo";
  notice: string | null;
}

export const AtlasDataContext = createContext<AtlasDataState>({
  data: null,
  loading: true,
  error: null,
  source: "loading",
  notice: null,
});

export function useAtlasData(): AtlasDataState {
  return useContext(AtlasDataContext);
}

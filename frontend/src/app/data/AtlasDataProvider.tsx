import { useState, useEffect, type ReactNode } from "react";
import { AtlasDataContext, type AtlasData, type AtlasDataState } from "./useAtlasData";
import * as mockData from "./mockData";

const DATA_URL = import.meta.env.VITE_DATA_URL as string | undefined;

export function AtlasDataProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AtlasDataState>({
    data: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      // If no remote URL configured, fall back to bundled mock data
      if (!DATA_URL) {
        setState({
          data: {
            communities: mockData.communities,
            channels: mockData.channels,
            edges: mockData.edges,
            overallStats: mockData.overallStats,
            topCommunitiesBySize: mockData.topCommunitiesBySize,
            mostConnectedChannels: mockData.mostConnectedChannels,
          },
          loading: false,
          error: null,
        });
        return;
      }

      try {
        const res = await fetch(DATA_URL);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json: AtlasData = await res.json();
        if (!cancelled) {
          setState({ data: json, loading: false, error: null });
        }
      } catch (err) {
        if (!cancelled) {
          // On fetch failure, fall back to mock data so the site still works
          console.warn("Failed to fetch live data, using mock data:", err);
          setState({
            data: {
              communities: mockData.communities,
              channels: mockData.channels,
              edges: mockData.edges,
              overallStats: mockData.overallStats,
              topCommunitiesBySize: mockData.topCommunitiesBySize,
              mostConnectedChannels: mockData.mostConnectedChannels,
            },
            loading: false,
            error: null,
          });
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AtlasDataContext.Provider value={state}>
      {children}
    </AtlasDataContext.Provider>
  );
}

import { useState, useEffect, type ReactNode } from "react";
import { AtlasDataContext, type AtlasData, type AtlasDataState } from "./useAtlasData";
import * as mockData from "./mockData";
import { validateAtlasData } from "./validateAtlasData";

const DATA_URL = import.meta.env.VITE_DATA_URL?.trim();
const MAX_RESPONSE_BYTES = 10 * 1024 * 1024;

const demoData: AtlasData = {
  communities: mockData.communities,
  channels: mockData.channels,
  edges: mockData.edges,
  overallStats: mockData.overallStats,
  topCommunitiesBySize: mockData.topCommunitiesBySize,
  mostConnectedChannels: mockData.mostConnectedChannels,
};

function resolveSameOriginDataUrl(value: string): string {
  const url = new URL(value, window.location.origin);
  if (url.origin !== window.location.origin || !["http:", "https:"].includes(url.protocol)) {
    throw new Error("VITE_DATA_URL must point to the same origin");
  }
  return url.toString();
}

export function AtlasDataProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AtlasDataState>({
    data: null,
    loading: true,
    error: null,
    source: "loading",
    notice: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!DATA_URL) {
        setState({
          data: demoData,
          loading: false,
          error: null,
          source: "demo",
          notice: "Portfolio preview using a bundled demonstration dataset.",
        });
        return;
      }

      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 10_000);

      try {
        const res = await fetch(resolveSameOriginDataUrl(DATA_URL), {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const contentLength = Number(res.headers.get("content-length") ?? 0);
        if (contentLength > MAX_RESPONSE_BYTES) throw new Error("Atlas data response is too large");

        const json: unknown = await res.json();
        const data = validateAtlasData(json);
        if (!cancelled) {
          setState({
            data,
            loading: false,
            error: null,
            source: "live",
            notice: null,
          });
        }
      } catch (err) {
        if (!cancelled) {
          console.warn("Live atlas data unavailable; using demonstration data.", err);
          setState({
            data: demoData,
            loading: false,
            error: "Live data could not be loaded.",
            source: "demo",
            notice: "Live data is unavailable; showing the bundled demonstration dataset.",
          });
        }
      } finally {
        window.clearTimeout(timeoutId);
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

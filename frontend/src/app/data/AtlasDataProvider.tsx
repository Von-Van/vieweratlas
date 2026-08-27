import { useState, useEffect, useRef, useCallback, type ReactNode } from "react";
import {
  ANALYSIS_WINDOWS,
  AtlasDataContext,
  DEFAULT_WINDOW,
  type AnalysisWindow,
  type AtlasData,
  type AtlasDataState,
} from "./useAtlasData";
import * as mockData from "./mockData";
import { validateAtlasData } from "./validateAtlasData";

const DATA_URL = import.meta.env.VITE_DATA_URL?.trim();
const MAX_RESPONSE_BYTES = 10 * 1024 * 1024;
const JSON_SUFFIX = /\.json$/i;

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

/**
 * Sibling key for one rolling window: `.../frontend-data.json` becomes
 * `.../frontend-data-90d.json`, matching what the exporter writes.
 *
 * Returns null for a configured URL that does not end in `.json`, which is the
 * signal that this deployment can only serve the single configured file.
 */
function windowedDataUrl(base: string, days: AnalysisWindow): string | null {
  if (!JSON_SUFFIX.test(base)) return null;
  return base.replace(JSON_SUFFIX, `-${days}d.json`);
}

async function fetchAtlasData(url: string, signal: AbortSignal): Promise<AtlasData> {
  const res = await fetch(resolveSameOriginDataUrl(url), {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    signal,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const contentLength = Number(res.headers.get("content-length") ?? 0);
  if (contentLength > MAX_RESPONSE_BYTES) throw new Error("Atlas data response is too large");
  return validateAtlasData(await res.json());
}

export function AtlasDataProvider({ children }: { children: ReactNode }) {
  const [activeWindow, setActiveWindow] = useState<AnalysisWindow>(DEFAULT_WINDOW);
  const [switching, setSwitching] = useState(false);
  const [state, setState] = useState<
    Omit<
      AtlasDataState,
      | "window"
      | "setWindow"
      | "windowAvailable"
      | "availableWindows"
      | "pendingWindows"
      | "windowPending"
      | "switching"
    >
  >({
    data: null,
    loading: true,
    error: null,
    source: "loading",
    notice: null,
  });

  // Windows already fetched and validated this session. Switching back to one
  // is instant and costs no request.
  const cache = useRef(new Map<AnalysisWindow, AtlasData>());
  // Mirrors activeWindow so setWindow can stay referentially stable: reading
  // state in the closure would need activeWindow as a dependency, which would
  // hand every consumer a new callback on each switch.
  const windowRef = useRef<AnalysisWindow>(DEFAULT_WINDOW);
  const requestSeq = useRef(0);
  const pending$ = useRef<AbortController | null>(null);
  // What the pipeline says it published, narrowed to windows this build knows
  // how to request. A payload that advertises nothing — a single-window export,
  // or anything predating the filter — leaves the control hidden rather than
  // offering buttons whose files were never written.
  const known = (days: number): days is AnalysisWindow =>
    (ANALYSIS_WINDOWS as readonly number[]).includes(days);
  const asc = (a: number, b: number) => a - b;

  const availableWindows = (state.data?.availableWindows ?? []).filter(known).sort(asc);
  const pendingWindows = (state.data?.pendingWindows ?? []).filter(known).sort(asc);
  // The control appears whenever the pipeline describes more than one window,
  // even if only one is loadable — the pending ones are the explanation for why
  // the others are not there yet.
  const windowAvailable =
    state.source === "live" &&
    !!DATA_URL &&
    JSON_SUFFIX.test(DATA_URL) &&
    availableWindows.length + pendingWindows.length > 1;
  const windowPending = pendingWindows.includes(activeWindow);

  // Initial load. The canonical window is fetched from the configured URL
  // itself rather than a derived one, so a single-window deployment — and any
  // build predating the window filter — keeps working untouched.
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
        const data = await fetchAtlasData(DATA_URL, controller.signal);
        if (!cancelled) {
          // The unsuffixed file is a copy of whichever window the pipeline made
          // default, which is not necessarily 30d while the data is still
          // filling out. Cache it under that window so switching away and back
          // does not refetch it, and open there.
          const opening = (data.defaultWindow ?? DEFAULT_WINDOW) as AnalysisWindow;
          cache.current.set(opening, data);
          windowRef.current = opening;
          setActiveWindow(opening);
          setState({ data, loading: false, error: null, source: "live", notice: null });
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

  const setWindow = useCallback(
    (days: AnalysisWindow) => {
      if (days === windowRef.current || !DATA_URL) return;

      // Pending windows have no published file. Select it so the button reads
      // as chosen and the map can explain itself, but do not request anything.
      const pending = state.data?.pendingWindows ?? [];
      if (pending.includes(days)) {
        pending$.current?.abort();
        requestSeq.current += 1;
        windowRef.current = days;
        setActiveWindow(days);
        setSwitching(false);
        setState((prev) => ({ ...prev, error: null, notice: null }));
        return;
      }

      const cached = cache.current.get(days);
      if (cached) {
        windowRef.current = days;
        setActiveWindow(days);
        setState((prev) => ({ ...prev, data: cached, error: null, notice: null }));
        return;
      }

      const url = windowedDataUrl(DATA_URL, days);
      if (!url) return;

      // Clicking through the windows faster than they load leaves several
      // fetches in flight. Without this, whichever resolves last wins rather
      // than whichever was clicked last, so a slow 14d response could land on
      // top of a 90d the viewer picked afterwards.
      pending$.current?.abort();
      const controller = new AbortController();
      pending$.current = controller;
      const seq = ++requestSeq.current;
      const isStale = () => seq !== requestSeq.current;

      const timeoutId = window.setTimeout(() => controller.abort(), 10_000);
      setSwitching(true);

      fetchAtlasData(url, controller.signal)
        .then((data) => {
          // Cache it regardless — the bytes are valid even if superseded.
          cache.current.set(days, data);
          if (isStale()) return;
          windowRef.current = days;
          setActiveWindow(days);
          setState((prev) => ({ ...prev, data, error: null, notice: null }));
        })
        .catch((err) => {
          if (isStale()) return;
          // The previous window is still on screen and still correct, so stay
          // on it rather than dropping the map to an error state.
          console.warn(`The ${days}-day window could not be loaded.`, err);
          setState((prev) => ({
            ...prev,
            error: `The ${days}-day window could not be loaded.`,
            notice: `The ${days}-day window is unavailable; showing ${windowRef.current} days.`,
          }));
        })
        .finally(() => {
          window.clearTimeout(timeoutId);
          // A superseded request must not clear the pending state out from
          // under the one that replaced it.
          if (!isStale()) {
            pending$.current = null;
            setSwitching(false);
          }
        });
    },
    [state.data],
  );

  return (
    <AtlasDataContext.Provider
      value={{
        ...state,
        window: activeWindow,
        setWindow,
        windowAvailable,
        availableWindows,
        pendingWindows,
        windowPending,
        switching,
      }}
    >
      {children}
    </AtlasDataContext.Provider>
  );
}

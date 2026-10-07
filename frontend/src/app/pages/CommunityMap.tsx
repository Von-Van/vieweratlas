import { useEffect, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { CommunityFigure } from "../components/CommunityFigure";
import { Loading } from "../components/Loading";
import { NotReady, PageFooter, usePageTitle } from "../components/Page";
import { useAtlasData, type AnalysisWindow, type AtlasData } from "../data/useAtlasData";
import { atlasIndex, communityName } from "../lib/atlas";
import { fmt, formatPeriod } from "../lib/format";
import { isSmall, SMALL_COLOR } from "../lib/palette";
import { LINK_CAP } from "../lib/snapshot";

export function CommunityMap() {
  usePageTitle("Community map");
  const { data, loading } = useAtlasData();
  if (loading || !data) return <Loading />;
  return <MapView data={data} />;
}

function MapView({ data }: { data: AtlasData }) {
  const {
    source,
    window: activeWindow,
    dataWindow,
    dataUrl,
    setWindow,
    windowAvailable,
    availableWindows,
    pendingWindows,
    windowPending,
    switching,
  } = useAtlasData();
  const index = atlasIndex(data);
  const [params, setParams] = useSearchParams();
  const focusedCommunity = params.get("community");

  // ?community=<id> opens with only that community shown; ?channel=<id>
  // opens with that channel selected. Both are how other pages link here.
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => {
    const only = focusedCommunity;
    if (!only || !index.community.has(only)) return new Set();
    return new Set(data.communities.filter((c) => c.id !== only).map((c) => c.id));
  });
  const [query, setQuery] = useState("");

  useEffect(() => {
    setHidden(
      focusedCommunity && index.community.has(focusedCommunity)
        ? new Set(data.communities.filter((c) => c.id !== focusedCommunity).map((c) => c.id))
        : new Set(),
    );
  }, [focusedCommunity, data, index]);

  // Read selection from the URL so links and browser history restore it.
  const picked = params.get("channel")?.toLowerCase()
    ?? (focusedCommunity ? index.members.get(focusedCommunity)?.[0]?.id ?? null : null);

  // A channel picked in one window may not exist in the next; fall back to
  // the best-connected channel rather than an empty panel.
  const selected = picked && index.channel.has(picked) ? picked : index.mostConnected[0]?.id ?? null;

  const select = (id: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("channel", id);
      return next;
    }, { replace: true, preventScrollReset: true });
  };

  const toggle = (ids: string[], show: boolean) =>
    setHidden((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (show) next.delete(id);
        else next.add(id);
      }
      return next;
    });

  const q = query.trim().toLowerCase();
  const matches = q
    ? data.channels.filter(
        (c) =>
          !hidden.has(c.communityId) &&
          (c.id.includes(q) || c.displayName.toLowerCase().includes(q)),
      )
    : [];
  const onSearchKey = (e: KeyboardEvent<HTMLInputElement>) => {
    // Channels arrive most-watched first, so the first match is the biggest.
    if (e.key === "Enter" && matches[0]) select(matches[0].id);
  };

  const big = index.communities.filter((c) => !isSmall(c));
  const small = index.communities.filter(isSmall);
  const empty = data.channels.length === 0;
  const fallback = availableWindows[availableWindows.length - 1];
  const windows = [...availableWindows, ...pendingWindows].sort((a, b) => a - b);

  const channel = selected ? index.channel.get(selected) : undefined;
  const community = channel ? index.community.get(channel.communityId) : undefined;
  const neighbours = channel ? index.neighbours.get(channel.id) ?? [] : [];
  const inside = channel ? index.inside.get(channel.id) ?? 0 : 0;

  const pickFromTable = (id: string) => (e: MouseEvent<HTMLAnchorElement>) => {
    // A plain click moves the selection; a modified click still opens the page.
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    select(id);
  };

  return (
    <div className="body body--tight">
      <div className="row row--wide">
        <div className="col" style={{ gap: 10 }}>
          <div className="selection-head" style={{ justifyContent: "space-between", gap: 16 }}>
            <h1 style={{ fontWeight: 600, fontSize: 24, lineHeight: 1.3 }}>Community map</h1>
            {!empty && !windowPending && (
              <span className="mono" style={{ fontSize: 13, color: "var(--muted)" }}>
                {fmt(data.channels.length)} channels · {fmt(data.edges.length)} links · ≤{LINK_CAP} per
                channel
              </span>
            )}
          </div>
          <CommunityFigure
            channels={data.channels}
            edges={data.edges}
            index={index}
            label={`Interactive community map of ${fmt(data.channels.length)} channels. Use the search box to find a channel.`}
            interactive
            hidden={hidden}
            selected={selected}
            query={query}
            onSelect={select}
          >
            {windowPending ? (
              <NotReady
                days={activeWindow}
                fallback={fallback}
                onFallback={fallback ? () => setWindow(fallback) : undefined}
              />
            ) : empty ? (
              <NotReady days={null} />
            ) : null}
          </CommunityFigure>
          <p className="caption">
            <strong>Figure 2.</strong> Same data as <Link to="/">Figure 1</Link>. Hover for a name, click
            to select a channel, and untick a community to hide it.
          </p>
        </div>

        <div className="controls">
          <fieldset className="control">
            <legend>window</legend>
            {windowAvailable ? (
              <select
                aria-label="Time window"
                value={activeWindow}
                onChange={(e) => setWindow(Number(e.target.value) as AnalysisWindow)}
              >
                {windows.map((days) => (
                  <option key={days} value={days}>
                    {days} days{pendingWindows.includes(days) ? " · pending" : ""}
                  </option>
                ))}
              </select>
            ) : (
              <p className="note" style={{ marginTop: 0, color: "var(--ink)" }}>
                {source === "demo" ? "demo dataset" : `${dataWindow} days`}
              </p>
            )}
            <p className="note">
              {windowPending
                ? `needs ${activeWindow} days of surveys`
                : switching
                  ? "loading…"
                  : formatPeriod(data.overallStats.collectionPeriod)}
            </p>
          </fieldset>

          <fieldset className="control">
            <legend>find a channel</legend>
            <input
              type="search"
              aria-label="Find a channel"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onSearchKey}
              placeholder={`e.g. ${index.mostConnected[0]?.id ?? "a channel"}`}
              autoComplete="off"
              spellCheck={false}
            />
            {q && (
              <p className="note" aria-live="polite">
                {matches.length === 0
                  ? "no matches"
                  : `${fmt(matches.length)} match${matches.length === 1 ? "" : "es"} · enter selects ${matches.length === 1 ? "it" : "the biggest"}`}
              </p>
            )}
          </fieldset>

          <fieldset className="control" style={{ paddingBottom: 10 }}>
            <legend>communities</legend>
            <div className="legend">
              {big.map((c) => (
                <label key={c.id}>
                  <input
                    type="checkbox"
                    checked={!hidden.has(c.id)}
                    onChange={(e) => toggle([c.id], e.target.checked)}
                  />
                  <span className="swatch" style={{ background: index.color.get(c.id) }} />
                  <span style={{ lineHeight: 1.3 }}>{c.label}</span>
                  <span className="count">{c.nodeCount}</span>
                </label>
              ))}
              {small.length > 0 && (
                <label>
                  <input
                    type="checkbox"
                    checked={small.some((c) => !hidden.has(c.id))}
                    onChange={(e) =>
                      toggle(
                        small.map((c) => c.id),
                        e.target.checked,
                      )
                    }
                  />
                  <span className="swatch" style={{ background: SMALL_COLOR }} />
                  <span style={{ lineHeight: 1.3 }}>
                    {small.length} smaller communit{small.length === 1 ? "y" : "ies"}
                  </span>
                  <span className="count">{small.reduce((sum, c) => sum + c.nodeCount, 0)}</span>
                </label>
              )}
            </div>
            <p className="legend-actions">
              <button type="button" className="linklike" onClick={() => setHidden(new Set())}>
                all
              </button>
              <button
                type="button"
                className="linklike"
                onClick={() => setHidden(new Set(data.communities.map((c) => c.id)))}
              >
                none
              </button>
            </p>
          </fieldset>
        </div>
      </div>

      {channel && community && !windowPending && (
        <div className="row row--wide" style={{ marginTop: 28 }}>
          <div className="col" style={{ gap: 10 }}>
            <div className="selection-head">
              <h2 className="section">{channel.displayName}</h2>
              <span style={{ fontSize: 16, color: "var(--muted)" }}>{communityName(community)}</span>
              <Link className="to-page" to={`/channel/${channel.id}`}>
                channel page →
              </Link>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Shares the most chatters with</th>
                    <th>Community</th>
                    <th className="num">Shared chatters</th>
                  </tr>
                </thead>
                <tbody>
                  {neighbours.slice(0, 6).map((n) => {
                    const other = index.channel.get(n.id);
                    if (!other) return null;
                    return (
                      <tr key={n.id}>
                        <td>
                          <a href={`/channel/${n.id}`} onClick={pickFromTable(n.id)}>
                            {other.displayName}
                          </a>
                        </td>
                        <td className="sub">
                          {other.communityId === channel.communityId
                            ? "same"
                            : communityName(index.community.get(other.communityId))}
                        </td>
                        <td className="num">{fmt(n.weight)}</td>
                      </tr>
                    );
                  })}
                  {neighbours.length === 0 && (
                    <tr>
                      <td colSpan={3} className="sub">
                        No links on the map.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <dl className="facts" style={{ paddingTop: 46 }}>
            <dt>mean viewers</dt>
            <dd>{fmt(channel.viewers)}</dd>
            <dt>links on map</dt>
            <dd>{neighbours.length}</dd>
            <dt>inside community</dt>
            <dd>
              {(inside / Math.max(1, neighbours.length)).toFixed(2)} ({inside} of {neighbours.length})
            </dd>
          </dl>
        </div>
      )}

      <PageFooter>
        <span>layout precomputed by the pipeline (spring layout inside each community)</span>
        <span>
          data:{" "}
          {dataUrl ? <a href={dataUrl}>{dataUrl.split("/").pop()}</a> : "bundled synthetic demo"}
        </span>
        <span>not affiliated with Twitch Interactive, Inc.</span>
      </PageFooter>
    </div>
  );
}

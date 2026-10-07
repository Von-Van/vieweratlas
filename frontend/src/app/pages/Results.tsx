import { Link } from "react-router";
import { Loading } from "../components/Loading";
import { PageFooter, Row, SnapshotNote, usePageTitle } from "../components/Page";
import { useAtlasData } from "../data/useAtlasData";
import { atlasIndex, communityName } from "../lib/atlas";
import { fmt, formatPeriod, isoDay } from "../lib/format";
import { repoFile } from "../lib/links";
import { isSmall } from "../lib/palette";
import { LINK_CAP, SNAPSHOT } from "../lib/snapshot";

const pct = (part: number, whole: number) => `${((part / Math.max(1, whole)) * 100).toFixed(1)}%`;

export function Results() {
  usePageTitle("Results");
  const {
    data,
    loading,
    source,
    dataWindow,
    dataUrl,
    setWindow,
    windowAvailable,
    availableWindows,
    pendingWindows,
    switching,
  } = useAtlasData();
  if (loading || !data) return <Loading />;

  const index = atlasIndex(data);
  const stats = data.overallStats;
  const live = source === "live";
  const name = live ? `${dataWindow}-day window` : "demo dataset";
  const updated = isoDay(data.generatedAt);
  const windows = [...availableWindows, ...pendingWindows].sort((a, b) => a - b);
  const largest = Math.max(1, ...index.communities.map((c) => c.nodeCount));
  const small = index.communities.filter(isSmall);
  const example =
    data.communities.find((c) => c.label.includes(" · "))?.label ?? "Variety (en) · Kaicenat, Jynxzi";

  const funnel = [
    { label: "distinct channels sampled", n: SNAPSHOT.channelsSampled },
    { label: "seen ≥3× with ≥10 chatters", n: SNAPSHOT.channelsEligible },
    { label: "in the overlap graph", n: SNAPSHOT.graphChannels },
    { label: "in a community of ≥10", n: SNAPSHOT.communityChannels },
    { label: "on the public map", n: SNAPSHOT.mapChannels },
  ];
  const tagged = SNAPSHOT.gameCommunities + SNAPSHOT.languageCommunities;

  return (
    <div className="body">
      <Row
        gap={10}
        asideTop={8}
        aside={
          <p>
            Every page on this site describes the same window, so the numbers never disagree with each
            other. Sections marked <span className="tag">SNAPSHOT</span> quote one fixed run instead.
          </p>
        }
      >
        <h1 className="title">Results</h1>
        <p className="mono" style={{ fontSize: 14, lineHeight: 1.7, display: "flex", flexWrap: "wrap", gap: "0 14px" }}>
          <span>window:</span>
          {windowAvailable ? (
            windows.map((days) =>
              pendingWindows.includes(days) ? (
                <span key={days} className="muted">
                  {days} days (not enough data yet)
                </span>
              ) : days === dataWindow ? (
                <span key={days} style={{ fontWeight: 600 }}>
                  {days} days
                </span>
              ) : (
                <button
                  key={days}
                  type="button"
                  className="linklike"
                  disabled={switching}
                  onClick={() => setWindow(days)}
                >
                  {days} days
                </button>
              ),
            )
          ) : (
            <span style={{ fontWeight: 600 }}>{live ? `${dataWindow} days` : "demo dataset"}</span>
          )}
        </p>
        <p className="pretty">
          {live
            ? `Surveys from ${formatPeriod(stats.collectionPeriod)}, analysed with the production settings. Everything not marked as a snapshot comes from the channel-level file the map draws${updated ? `, generated on ${updated}` : ""}.`
            : "A synthetic dataset from make_demo_data.py, run through the same pipeline. Every channel in it is invented."}
        </p>
      </Row>

      <Row
        top={44}
        gap={14}
        asideTop={44}
        aside={
          <p>
            That run used an overlap threshold of {SNAPSHOT.threshold} shared chatters. The thresholds in
            production now are in <Link to="/methods#threshold">methods §&nbsp;4</Link>.
          </p>
        }
      >
        <h2 className="section">1. From raw samples to the map</h2>
        <SnapshotNote />
        <figure className="figure">
          <div className="figbox">
            {funnel.map((step) => (
              <div key={step.label} className="bar-row">
                <span>{step.label}</span>
                <div className="bar" style={{ width: pct(step.n, SNAPSHOT.channelsSampled) }} />
                <span className="val">{fmt(step.n)}</span>
              </div>
            ))}
          </div>
          <figcaption className="caption">
            <strong>Figure 3.</strong> Channels kept at each step of the 14-day run. The full overlap
            graph has {fmt(SNAPSHOT.graphEdges)} edges. The public map keeps {fmt(SNAPSHOT.mapEdges)} of
            them, at most {LINK_CAP} per channel.
          </figcaption>
        </figure>
        <p className="pretty">
          In the {name}, {fmt(stats.totalChannels)} channels are in a community of ten or more, and{" "}
          {fmt(data.channels.length)} of them are on the map with {fmt(data.edges.length)} links between
          them.
        </p>
      </Row>

      <Row
        top={44}
        gap={14}
        asideTop={44}
        aside={
          <p>
            Labels come from the dominant game or language. When two communities would get the same
            label, each is also named after its two largest channels, for example <em>{example}</em>.
          </p>
        }
      >
        <h2 className="section">2. Communities</h2>
        <figure className="figure">
          <div className="figbox figbox--communities">
            {index.communities.map((c) => (
              <div key={c.id} className="bar-row">
                <span>{communityName(c)}</span>
                <div
                  className="bar"
                  style={{ width: pct(c.nodeCount, largest), background: index.color.get(c.id) }}
                />
                <span className="val">{c.nodeCount}</span>
              </div>
            ))}
          </div>
          <figcaption className="caption">
            <strong>Figure 4.</strong> Channels per community on the public map (
            {fmt(data.channels.length)} channels, {data.communities.length} communities).
            {small.length > 0 && (
              <>
                {" "}
                The {small.length === 1 ? "grey bar is a community" : `${small.length} grey bars are communities`}{" "}
                with fewer than ten channels <em>on the map</em>. Each has at least ten in the full graph.
              </>
            )}
          </figcaption>
        </figure>
        <SnapshotNote />
        <p className="pretty">
          Each of the {SNAPSHOT.communities} communities in that run's full graph has one dominant
          attribute. {SNAPSHOT.gameCommunities} are mostly one game (≥60% of channels). The other{" "}
          {SNAPSHOT.languageCommunities} mostly share a broadcast language (≥40%).
        </p>
        <div className="split" style={{ color: "#fff" }}>
          <div style={{ width: pct(SNAPSHOT.gameCommunities, tagged), background: "var(--bar)" }}>
            {SNAPSHOT.gameCommunities} · one game
          </div>
          <div style={{ width: pct(SNAPSHOT.languageCommunities, tagged), background: "var(--bar-2)" }}>
            {SNAPSHOT.languageCommunities} · one language
          </div>
        </div>
        <p className="pretty">
          Neither game nor language is an input to Louvain, so this agreement suggests the overlap signal
          reflects real structure. It doesn't explain <em>why</em> audiences cluster.
        </p>
      </Row>

      <Row
        top={44}
        gap={14}
        asideTop={44}
        aside={
          <p>
            How bots are detected, and what the filter gets wrong:{" "}
            <Link to="/methods#bots">methods §&nbsp;2</Link>.
          </p>
        }
      >
        <h2 className="section">3. Bots</h2>
        <SnapshotNote />
        <figure className="figure">
          <div className="figbox" style={{ gap: 14 }}>
            <div className="share">
              <span>share of all chatters</span>
              <div className="share__track">
                <div className="share__fill" style={{ width: SNAPSHOT.bots.shareOfChatters }} />
              </div>
              <span className="share__note">
                bots {SNAPSHOT.bots.shareOfChatters} ({SNAPSHOT.bots.accounts} accounts) · people 99.97%
              </span>
            </div>
            <div className="share">
              <span>share of channel-pair overlap</span>
              <div className="share__track">
                <div className="share__fill" style={{ width: `${SNAPSHOT.bots.shareOfOverlap}%` }} />
              </div>
              <span className="share__note">
                bots {SNAPSHOT.bots.shareOfOverlap}% · people {100 - SNAPSHOT.bots.shareOfOverlap}%
              </span>
            </div>
          </div>
          <figcaption className="caption">
            <strong>Figure 5.</strong> Before filtering, {SNAPSHOT.bots.accounts} accounts supplied{" "}
            {SNAPSHOT.bots.shareOfOverlap}% of every channel-pair overlap increment. Left in, they would
            add {SNAPSHOT.bots.edgesAdded} edges ({SNAPSHOT.bots.shareOfUnfilteredEdges}% of the
            unfiltered graph) linking channels through bots alone.
          </figcaption>
        </figure>
      </Row>

      <Row top={44} gap={14}>
        <h2 className="section">4. Most people chat in one place</h2>
        <SnapshotNote />
        <div className="split">
          <div style={{ width: `${SNAPSHOT.oneChannelShare}%`, background: "var(--track)", color: "var(--ink)" }}>
            {SNAPSHOT.oneChannelShare}% · one channel only
          </div>
          <div style={{ width: `${100 - SNAPSHOT.oneChannelShare}%`, background: "var(--ink)", color: "#fff" }}>
            2+
          </div>
        </div>
        <p className="pretty">
          Of {fmt(SNAPSHOT.chatters)} chatters, about {fmt(SNAPSHOT.multiChannelChatters)} appeared in two
          or more channels. Every edge on the map comes from them.
        </p>
      </Row>

      <Row
        top={44}
        asideTop={44}
        aside={
          <p>
            The map caps each channel at {LINK_CAP} links, so many channels tie on links. Ties are broken
            by the shared chatters summed across those links.
          </p>
        }
      >
        <h2 className="section">5. Most connected channels</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th className="rank">#</th>
                <th>Channel</th>
                <th>Community</th>
                <th className="num">Links</th>
                <th className="num">Shared chatters</th>
              </tr>
            </thead>
            <tbody>
              {index.mostConnected.slice(0, 8).map((c, i) => (
                <tr key={c.id}>
                  <td className="rank">{i + 1}</td>
                  <td>
                    <Link to={`/channel/${c.id}`}>{c.displayName}</Link>
                  </td>
                  <td className="sub">{communityName(index.community.get(c.communityId))}</td>
                  <td className="num">{index.neighbours.get(c.id)?.length ?? 0}</td>
                  <td className="num">{fmt(index.sharedTotal.get(c.id) ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Row>

      <PageFooter>
        <span>
          data for this page:{" "}
          {dataUrl ? <a href={dataUrl}>{dataUrl.split("/").pop()}</a> : "bundled synthetic demo"}{" "}
          (channel-level only)
        </span>
        <span>
          field definitions: <a href={repoFile("docs/metrics.md")}>metrics.md</a>
        </span>
      </PageFooter>
    </div>
  );
}

import { Link, useParams } from "react-router";
import { Loading } from "../components/Loading";
import { PageFooter, Row, usePageTitle } from "../components/Page";
import { ViewerChart } from "../components/ViewerChart";
import { useAtlasData } from "../data/useAtlasData";
import { atlasIndex, communityName } from "../lib/atlas";
import { fmt, formatPeriod, languageName, periodDays } from "../lib/format";
import { isSmall } from "../lib/palette";
import { LINK_CAP } from "../lib/snapshot";

export function ChannelDetail() {
  const { id } = useParams<{ id: string }>();
  const {
    data,
    loading,
    source,
    dataWindow,
    setWindow,
    windowAvailable,
    availableWindows,
    switching,
  } = useAtlasData();
  const index = data ? atlasIndex(data) : null;
  const channel = id && index ? index.channel.get(id.toLowerCase()) : undefined;
  const otherWindows = windowAvailable ? availableWindows.filter((days) => days !== dataWindow) : [];
  usePageTitle(
    channel?.displayName ?? (data ? (otherWindows.length ? "Not in this window" : "Channel not found") : null),
  );

  if (loading || !data || !index) return <Loading />;

  const live = source === "live";
  const name = live ? `${dataWindow}-day window` : "demo dataset";
  const windowLinks = otherWindows.map((days, i) => (
    <span key={days}>
      {i > 0 && (i === otherWindows.length - 1 ? " or the " : ", the ")}
      <button type="button" className="linklike" disabled={switching} onClick={() => setWindow(days)}>
        {days}-day window
      </button>
    </span>
  ));

  const community = channel ? index.community.get(channel.communityId) : undefined;
  if (!channel || !community) {
    return (
      <div className="body body--tight">
        <p className="mono-line">
          <Link to="/map">map</Link> / {id}
        </p>
        <Row top={14} gap={16}>
          <h1 style={{ fontWeight: 700, fontSize: 34, lineHeight: 1.2 }}>
            {otherWindows.length ? "Not in this window" : "Channel not found"}
          </h1>
          <p className="pretty">
            {otherWindows.length ? (
              <>
                “{id}” isn't on the map for the {name}. Each window keeps only the channels seen often
                enough in it, so try the {windowLinks}.
              </>
            ) : (
              <>“{id}” isn't on the map. The map only holds the most-watched channels that share chatters with others.</>
            )}
          </p>
          <p style={{ fontSize: 16 }}>
            <Link to="/map">Back to the map</Link>
          </p>
        </Row>
      </div>
    );
  }

  const color = index.color.get(community.id) ?? "#1f77b4";
  const neighbours = index.neighbours.get(channel.id) ?? [];
  const inside = index.inside.get(channel.id) ?? 0;
  // Days in the window, when the history is dated in step with it. Payloads
  // from before per-day history carry one undated point and skip this.
  const windowDays = periodDays(data.overallStats.collectionPeriod);
  const days =
    windowDays && channel.viewerHistory.every((p) => windowDays.includes(p.date)) ? windowDays : null;
  const seenDays = channel.viewerHistory.length;
  const others = (index.members.get(community.id) ?? []).filter((c) => c.id !== channel.id);
  const topShared = neighbours[0]?.weight ?? 1;

  return (
    <div className="body body--tight">
      <p className="mono-line" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Link to="/map">map</Link>
        <span>/</span>
        <Link to={`/map?community=${encodeURIComponent(community.id)}`}>{communityName(community)}</Link>
        <span>/</span>
        <span style={{ color: "var(--ink)" }}>{channel.name}</span>
      </p>

      <Row
        top={14}
        gap={16}
        asideTop={62}
        aside={
          <>
            <p>Viewer counts come from Twitch's own API at the start of each survey. They don't come from chat.</p>
            {otherWindows.length > 0 && (
              <p>
                Other windows can tell a different story. Try the {windowLinks}.
              </p>
            )}
          </>
        }
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
          <h1 style={{ fontWeight: 700, fontSize: 34, lineHeight: 1.2 }}>{channel.displayName}</h1>
          <a className="mono" style={{ fontSize: 14 }} href={`https://twitch.tv/${channel.name}`}>
            twitch.tv/{channel.name}
          </a>
        </div>
        <dl className="facts facts--ruled">
          <dt>community</dt>
          <dd>
            {communityName(community)}{" "}
            <span className="muted">({community.nodeCount} channels on the map)</span>
          </dd>
          <dt>language</dt>
          <dd>{languageName(channel.language)}</dd>
          <dt>most-streamed category</dt>
          <dd>{channel.game}</dd>
          <dt>mean concurrent viewers</dt>
          <dd className="num">{fmt(channel.viewers)}</dd>
          {days && (
            <>
              <dt>seen live on</dt>
              <dd className="num">
                {seenDays} of {days.length} days
              </dd>
            </>
          )}
          <dt>links on the map</dt>
          <dd className="num">
            {neighbours.length}
            {neighbours.length >= LINK_CAP && <span className="aside-text"> (the cap)</span>}
          </dd>
          <dt>in-community link share</dt>
          <dd className="num">
            {(inside / Math.max(1, neighbours.length)).toFixed(2)}{" "}
            <span className="aside-text">
              ({inside} of {neighbours.length} links stay inside its community)
            </span>
          </dd>
        </dl>
      </Row>

      <Row
        top={36}
        asideTop={4}
        aside={
          <p>
            Each point averages that day's surveys at 06:00, 14:00 and 22:00 ET. A channel that streams
            at other hours looks smaller here than it is.
          </p>
        }
      >
        <figure className="figure">
          <ViewerChart
            history={channel.viewerHistory}
            days={days}
            color={color}
            label={`Daily mean concurrent viewers for ${channel.displayName}.`}
          />
          <figcaption className="caption">
            <strong>Figure 6.</strong> Mean concurrent viewers on each day of the {name} (
            {formatPeriod(data.overallStats.collectionPeriod)}).
            {days && seenDays < days.length && " Hollow markers are days the channel wasn't seen live in any survey."}
          </figcaption>
        </figure>
      </Row>

      <Row top={40}>
        <h2 className="section">Shares the most chatters with</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Channel</th>
                <th>Community</th>
                <th className="num" style={{ paddingRight: 14 }}>
                  Shared
                </th>
                <th className="meter" aria-hidden="true" />
              </tr>
            </thead>
            <tbody>
              {neighbours.slice(0, 8).map((n) => {
                const other = index.channel.get(n.id);
                if (!other) return null;
                return (
                  <tr key={n.id}>
                    <td>
                      <Link to={`/channel/${n.id}`}>{other.displayName}</Link>
                    </td>
                    <td className="sub">
                      {other.communityId === channel.communityId
                        ? "same"
                        : communityName(index.community.get(other.communityId))}
                    </td>
                    <td className="num" style={{ paddingRight: 14, fontSize: 14 }}>
                      {fmt(n.weight)}
                    </td>
                    <td className="meter" aria-hidden="true">
                      <div
                        style={{
                          width: `${(n.weight / topShared) * 100}%`,
                          background: index.color.get(other.communityId),
                        }}
                      />
                    </td>
                  </tr>
                );
              })}
              {neighbours.length === 0 && (
                <tr>
                  <td colSpan={4} className="sub">
                    No links on the map.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="pretty" style={{ fontSize: 16, color: "var(--caption)" }}>
          These counts are lower bounds that grow with the number of surveys. Compare rows in this table
          with each other. Don't compare them with this channel's page in another window.
        </p>
      </Row>

      {others.length > 0 && (
        <Row top={36} gap={8}>
          <h2 className="section">Others in this community</h2>
          <p className="pretty">
            {others.slice(0, 8).map((c, i, shown) => (
              <span key={c.id}>
                {i > 0 && (i === shown.length - 1 && others.length === shown.length ? " and " : ", ")}
                <Link to={`/channel/${c.id}`}>{c.displayName}</Link>
              </span>
            ))}
            {others.length > 8 && (
              <>
                {" "}
                and{" "}
                <Link to={`/map?community=${encodeURIComponent(community.id)}`}>
                  {others.length - 8} more on the map
                </Link>
              </>
            )}
            .
            {isSmall(community) && " This community is grey on the map: it's too small there to name."}
          </p>
        </Row>
      )}

      <PageFooter>
        <span>
          {name} · {formatPeriod(data.overallStats.collectionPeriod)}
        </span>
        <span>channel-level aggregates only</span>
        <span>not affiliated with Twitch Interactive, Inc.</span>
      </PageFooter>
    </div>
  );
}

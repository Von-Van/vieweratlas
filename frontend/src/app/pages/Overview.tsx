import { Link } from "react-router";
import { CommunityFigure } from "../components/CommunityFigure";
import { Loading } from "../components/Loading";
import { NotReady, PageFooter, Row, SnapshotNote, usePageTitle } from "../components/Page";
import { useAtlasData } from "../data/useAtlasData";
import { atlasIndex } from "../lib/atlas";
import { fmt, formatPeriod, isoDay } from "../lib/format";
import { ISSUES_URL, REPO_URL, TWITCH_ATLAS_URL, TWITCHMAP_URL } from "../lib/links";
import { SNAPSHOT } from "../lib/snapshot";

/** "14", "14- and 30", "14-, 30- and 90": the prefix of "-day windows". */
function windowList(days: number[]): string {
  if (days.length < 2) return days.join("");
  return `${days.slice(0, -1).join("-, ")}- and ${days[days.length - 1]}`;
}

export function Overview() {
  usePageTitle(null);
  const { data, loading, source, dataWindow, windowAvailable, availableWindows, pendingWindows } =
    useAtlasData();
  if (loading || !data) return <Loading />;

  const index = atlasIndex(data);
  const stats = data.overallStats;
  const period = formatPeriod(stats.collectionPeriod);
  const live = source === "live";
  const name = live ? `${dataWindow}-day window` : "demo dataset";
  const empty = data.channels.length === 0;
  const updated = isoDay(data.generatedAt);

  return (
    <div className="body">
      <Row
        gap={14}
        asideTop={6}
        aside={
          <>
            <p>
              <span className="tag">STATUS</span>
            </p>
            {live ? (
              <p>
                Collection runs 3× daily.
                {availableWindows.length > 0 &&
                  ` The ${windowList(availableWindows)}-day window${availableWindows.length > 1 ? "s are" : " is"} published.`}
                {pendingWindows.map((days) => ` The ${days}-day window publishes itself once ${days} days of surveys exist.`)}
              </p>
            ) : (
              <p>Every channel in this preview is invented. The real map loads once live data is available.</p>
            )}
          </>
        }
      >
        <p className="lede">
          Three times a day, ViewerAtlas notes who sends chat messages in the ~1,200 most-watched live
          Twitch channels. Two channels are linked when the same people chat in both. Groups of channels
          that share far more chatters with each other than with anyone else are drawn as communities.
        </p>
        <p className="mono-line">
          showing: {name} · {period}
          {windowAvailable && (
            <>
              {" "}
              · <Link to="/results">other windows</Link>
            </>
          )}
        </p>
      </Row>

      <Row
        top={32}
        asideTop={4}
        aside={
          <>
            <p>
              <em>How to read it.</em> Each community gets its own disc, and channels are laid out inside
              it. The distance between two discs means nothing. The lines between them do.
            </p>
            <p>
              A "chatter" is anyone who sent at least one message during a sampled five-minute window.
              People who only watch don't show up.
            </p>
          </>
        }
      >
        <figure className="figure">
          {empty ? (
            <NotReady days={null} inline />
          ) : (
            <CommunityFigure
              channels={data.channels}
              edges={data.edges}
              index={index}
              label={`Community map of ${fmt(data.channels.length)} channels in ${data.communities.length} communities.`}
            />
          )}
          <figcaption className="caption">
            <strong>Figure 1.</strong> The {fmt(data.channels.length)} most-watched channels from the{" "}
            {name}, coloured by detected community. A line means at least {fmt(index.minWeight)}{" "}
            {index.minWeight === 1 ? "person" : "people"} chatted in both channels during the sampled
            windows. Dot size is mean concurrent viewers. <Link to="/map">Interactive version →</Link>
          </figcaption>
        </figure>
      </Row>

      <Row
        top={44}
        asideTop={42}
        aside={
          <p>
            <sup>1</sup> One "overlap increment" = one shared chatter added to one channel pair. A bot
            chatting in 40 channels creates 780 of them.
          </p>
        }
      >
        <h2 className="section">What I found (so far)</h2>
        <SnapshotNote />
        <ol className="list">
          <li>
            <strong>Bots make up most of the raw overlap.</strong> {SNAPSHOT.bots.accounts} accounts,{" "}
            {SNAPSHOT.bots.shareOfChatters} of all chatters, produced {SNAPSHOT.bots.shareOfOverlap}% of
            the channel-pair overlap.<sup>1</sup> Left in, they add {SNAPSHOT.bots.edgesAdded} edges that
            connect channels through bots alone.
          </li>
          <li>
            <strong>Communities line up with language and game.</strong> Each community has one dominant
            attribute. {SNAPSHOT.gameCommunities} have a single game on at least 60% of their channels,
            and the other {SNAPSHOT.languageCommunities} share a broadcast language on at least 40%.
            Neither attribute is an input to the algorithm.
          </li>
          <li>
            <strong>The signal is thin.</strong> {SNAPSHOT.oneChannelShare}% of chatters only ever appear
            in one channel. The whole graph rests on the ~{fmt(SNAPSHOT.multiChannelChatters)} who appear in two
            or more.
          </li>
        </ol>
        <p style={{ fontSize: 16 }}>
          <Link to="/results">Full results, with figures →</Link>
        </p>
      </Row>

      <Row
        top={44}
        asideTop={42}
        aside={
          <p>
            A channel sample is one channel in one survey. A channel that was live and in the top ~1,200
            for every survey in the window contributes one per survey.
          </p>
        }
      >
        <h2 className="section">The {name} in numbers</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Step</th>
                <th className="num">Result</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Survey days</td>
                <td className="num">{period}</td>
              </tr>
              <tr>
                <td>Channel samples</td>
                <td className="num">{fmt(stats.dataPoints)}</td>
              </tr>
              <tr>
                <td>Distinct chatters, bots removed</td>
                <td className="num">{fmt(stats.totalViewers)}</td>
              </tr>
              <tr>
                <td>Communities of ≥10 channels</td>
                <td className="num">
                  {fmt(stats.communitiesDetected)} · modularity {stats.modularityScore.toFixed(2)}
                </td>
              </tr>
              <tr>
                <td>Channels in those communities</td>
                <td className="num">
                  {fmt(stats.totalChannels)} · {fmt(stats.edgesTotal)} edges
                </td>
              </tr>
              <tr>
                <td>Public map (Figure 1)</td>
                <td className="num">
                  {fmt(data.channels.length)} channels · {fmt(data.edges.length)} edges ·{" "}
                  {data.communities.length} communities
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Row>

      <Row top={44}>
        <h2 className="section">Read this before quoting a number</h2>
        <ul className="list">
          <li>
            It counts <em>chatters</em>, not viewers. Chat-heavy channels and cultures are
            over-represented.
          </li>
          <li>
            It's a sample: three five-minute windows a day, at fixed US Eastern times. Other hours and
            time zones are under-covered.
          </li>
          <li>
            Shared-chatter counts are lower bounds, and they grow with the number of surveys. Compare
            channel pairs within one window, not across windows.
          </li>
          <li>
            It shows <em>where</em> audiences overlap, not why. Raids, recommendations and migration
            aren't in the data.
          </li>
        </ul>
        <p style={{ fontSize: 16 }}>
          Full list, with what I do about each: <Link to="/methods#limitations">methods §&nbsp;8</Link>.
        </p>
      </Row>

      <Row top={44}>
        <h2 className="section">Related work</h2>
        <ul className="list">
          <li>
            <a href={TWITCH_ATLAS_URL}>Twitch Atlas</a> (Kiran Gershenfeld, 2020) is the original. It
            linked streamers whose hourly viewer lists overlapped by more than 300 people. On five-minute
            chat samples that threshold produces no edges at all, so ViewerAtlas measures its thresholds
            from its own data.
          </li>
          <li>
            <a href={TWITCHMAP_URL}>twitchmap</a> listens to the top ~1,000 channels around the clock
            and weights loyal chatters more. ViewerAtlas samples instead, counts every shared chatter
            equally, and publishes all of its code.
          </li>
        </ul>
      </Row>

      <Row
        top={44}
        asideTop={42}
        aside={
          <>
            <p style={{ color: "var(--ink)" }}>If you use this:</p>
            <p style={{ font: "400 12.5px/1.55 var(--mono)" }}>
              Von-Van (2026). ViewerAtlas: mapping shared Twitch chat audiences.
              github.com/Von-Van/vieweratlas
            </p>
          </>
        }
      >
        <h2 className="section">Code and data</h2>
        <p className="pretty">
          Everything is on <a href={REPO_URL}>GitHub</a> under MIT: collection, analysis, and this site.
          You can run the whole pipeline on synthetic data without a Twitch account:
        </p>
        <pre className="code">{`git clone https://github.com/Von-Van/vieweratlas.git
cd vieweratlas/twitchiobot
python scripts/make_demo_data.py
STORAGE_TYPE=file LOGS_DIR=demo_data python src/main.py analyze rigorous`}</pre>
        <p className="pretty">
          Raw survey files contain Twitch user IDs and logins, so they stay private and are deleted after
          100 days. This site only ever receives channel-level totals.
        </p>
      </Row>

      <PageFooter>
        {updated && <span>data generated {updated}</span>}
        <span>MIT licence</span>
        <span>not affiliated with Twitch Interactive, Inc.</span>
        <span>
          corrections: <a href={ISSUES_URL}>open an issue</a>
        </span>
      </PageFooter>
    </div>
  );
}

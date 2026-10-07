import { Link } from "react-router";
import { PageFooter, Row, usePageTitle } from "../components/Page";
import { useAtlasData } from "../data/useAtlasData";
import { fmt, isoDay } from "../lib/format";
import { ISSUES_URL, repoFile } from "../lib/links";
import { LINK_CAP, SNAPSHOT, THRESHOLDS } from "../lib/snapshot";

const CONTENTS = [
  ["sampling", "Sampling"],
  ["bots", "Removing bots"],
  ["graph", "Building the graph"],
  ["threshold", "Choosing a threshold"],
  ["communities", "Finding communities"],
  ["validation", "Does it work?"],
  ["privacy", "Privacy"],
  ["limitations", "Limitations"],
  ["stack", "Stack"],
  ["faq", "FAQ"],
] as const;

const src = (file: string) => repoFile(`twitchiobot/src/${file}`);

function Section({ id, n, title }: { id: string; n: number; title: string }) {
  return (
    <h2 id={id} style={{ fontWeight: 600, fontSize: 22, lineHeight: 1.3, scrollMarginTop: 16 }}>
      {n}. {title}
    </h2>
  );
}

export function Methods() {
  usePageTitle("Methods");
  const { data, source, dataWindow } = useAtlasData();
  const updated = isoDay(data?.generatedAt);

  return (
    <div className="body">
      <div className="row">
        <div className="col" style={{ gap: 14 }}>
          <h1 className="title">Methods</h1>
          <p className="pretty" style={{ fontSize: 20, lineHeight: 1.5 }}>
            How the map is made, and what it can't tell you. The long version, with links to the code for
            every step, is <a href={repoFile("docs/methodology.md")}>methodology.md</a>.
          </p>
        </div>
        <nav className="aside" aria-label="Contents" style={{ fontSize: 15, color: "var(--ink)", paddingTop: 6, gap: 0 }}>
          <p className="mono" style={{ fontWeight: 600, fontSize: 12, marginBottom: 6 }}>
            CONTENTS
          </p>
          <ol className="list" style={{ gap: 1 }}>
            {CONTENTS.map(([id, title]) => (
              <li key={id}>
                {/* A router link rather than a bare #fragment: the router would
                    read a bare one as a fresh page load and restore the top. */}
                <Link to={{ hash: id }}>{title}</Link>
              </li>
            ))}
          </ol>
        </nav>
      </div>

      <Row top={32}>
        <figure className="figure">
          <pre className="diagram">{`Twitch Helix + EventSub
   │  3× daily: 12 batches of ≤100 channels,
   │  one shared five-minute window per batch
   ▼
private Parquet + manifest (S3, deleted after 100 days)
   │  each window → remove bots → overlap graph
   │              → Louvain → labels
   ▼
public JSON: channel-level only (S3 + CloudFront)
   │
   ▼
this website (14 / 30 / 90-day windows)`}</pre>
          <figcaption className="caption">
            <strong>Figure 7.</strong> The pipeline. Each stage is a one-shot job, and the stages only
            talk to each other through S3. Nothing runs continuously.
          </figcaption>
        </figure>
      </Row>

      <Row
        top={40}
        gap={10}
        asideTop={40}
        aside={
          <p>
            Code: <a href={src("eventsub_survey.py")}>eventsub_survey.py</a>,{" "}
            <a href={src("update_channels.py")}>update_channels.py</a>
          </p>
        }
      >
        <Section id="sampling" n={1} title="Sampling" />
        <p className="pretty">
          Surveys start at 06:00, 14:00 and 22:00 US Eastern and take about 80 minutes. Each survey takes
          the ~1,200 most-watched live channels, splits them into 12 batches of up to 100, and listens to
          each batch for one shared five-minute window. For every channel it records who sent at least one
          message (user ID and login, never the message text), plus viewers, category and language at the
          start.
        </p>
      </Row>

      <Row
        top={32}
        gap={10}
        asideTop={40}
        aside={
          <p>
            Code: <a href={src("chatter_filter.py")}>chatter_filter.py</a>
          </p>
        }
      >
        <Section id="bots" n={2} title="Removing bots" />
        <p className="pretty">
          A few accounts chat in dozens of channels at once. Anyone seen in four or more surveyed chats
          within the same five minutes is treated as automated and dropped, along with a list of known
          bots. In the 14-day sample that removed {SNAPSHOT.bots.accounts} accounts. The filter can drop a
          rare, very busy human, and it can miss bots that aren't on the list.
        </p>
      </Row>

      <Row
        top={32}
        gap={10}
        asideTop={40}
        aside={<p>Normalised weights (Jaccard, overlap coefficient) are implemented and tested, but they aren't used yet.</p>}
      >
        <Section id="graph" n={3} title="Building the graph" />
        <p className="pretty">
          For each window (14, 30 or 90 days), I take the union of each channel's chatters across every
          survey in the window. I keep channels seen at least 3 times with at least 10 chatters, then count
          the shared chatters for every pair of channels. That count is the edge weight. Every shared
          chatter counts the same.
        </p>
      </Row>

      <Row
        top={32}
        gap={12}
        asideTop={40}
        aside={
          <p>
            <span style={{ background: "var(--mark)", color: "var(--ink)", padding: "0 3px" }}>To do:</span>{" "}
            re-measure all three once the 90-day window fills, around 11 November 2026. This is first on the
            roadmap.
          </p>
        }
      >
        <Section id="threshold" n={4} title="Choosing a threshold" />
        <p className="pretty">
          Twitch Atlas used 300 shared viewers. On five-minute chat samples that leaves no edges at all, so
          each window's threshold is measured from its own data. Each is the window's 90th-percentile pair
          overlap, so every line on the map is in the top tenth of channel pairs. Where modularity peaks
          would be stricter (10, 9 and 25), but far fewer channels stay connected: 28% instead of 55% in
          the 14-day window.
        </p>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Window</th>
                <th>Min. shared chatters</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {THRESHOLDS.map((t) => (
                <tr key={t.window}>
                  <td className="code-cell" style={{ fontSize: 14.5 }}>
                    {t.window} days
                  </td>
                  <td className="code-cell" style={{ fontSize: 14.5 }}>
                    {t.value}
                  </td>
                  <td>{t.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Row>

      <Row
        top={32}
        gap={10}
        asideTop={40}
        aside={
          <p>
            Code: <a href={src("community_detector.py")}>community_detector.py</a>,{" "}
            <a href={src("cluster_tagger.py")}>cluster_tagger.py</a>
          </p>
        }
      >
        <Section id="communities" n={5} title="Finding communities" />
        <p className="pretty">
          Louvain modularity optimisation (python-louvain) runs on the weighted graph. Communities smaller
          than 10 channels are dropped. Each remaining community is named after its dominant game if one
          covers at least 60% of its channels, and otherwise after its dominant language (40% or more).
          The 14-day run of 13–26 August 2026 had {SNAPSHOT.communities} communities and a modularity of{" "}
          {SNAPSHOT.modularity}.
        </p>
      </Row>

      <Row top={32} gap={10}>
        <Section id="validation" n={6} title="Does it work?" />
        <p className="pretty">
          On synthetic data with eight planted communities (
          <a href={repoFile("twitchiobot/scripts/make_demo_data.py")}>make_demo_data.py</a>), the pipeline
          recovers all eight. On real data, the communities it finds agree with language and game, even
          though neither is an input. That's a sanity check, not proof.
        </p>
      </Row>

      <Row
        top={32}
        gap={10}
        asideTop={40}
        aside={
          <p>
            <a href={repoFile("twitchiobot/docs/DATA_POLICY.md")}>Data policy</a>
          </p>
        }
      >
        <Section id="privacy" n={7} title="Privacy" />
        <p className="pretty">
          Raw survey files hold Twitch user IDs and logins, which count as personal data. They stay in a
          private bucket and are deleted after 100 days. Message text, lurkers, message counts and
          per-message timestamps are never stored. The public site only receives channel-level aggregates,
          and a check runs after every deploy to make sure no chatter identities are in them.
        </p>
      </Row>

      <Row top={32} gap={10}>
        <Section id="limitations" n={8} title="Limitations" />
        <ul className="list" style={{ gap: 6 }}>
          <li>
            <strong>Chatters, not viewers.</strong> Lurkers are invisible.
          </li>
          <li>
            <strong>A sample, not a census.</strong> Three five-minute windows a day at fixed US Eastern
            times, top ~1,200 channels only.
          </li>
          <li>
            <strong>Counts depend on effort.</strong> Overlaps grow faster than linearly with the number of
            surveys, so only compare pairs within one window.
          </li>
          <li>
            <strong>Matched by login.</strong> A renamed account counts as two people.
          </li>
          <li>
            <strong>Calibration is a judgement call.</strong> See <Link to={{ hash: "threshold" }}>§&nbsp;4</Link>.
          </li>
          <li>
            <strong>Observational.</strong> It shows where audiences overlap, not why.
          </li>
          <li>
            <strong>A projection.</strong>{" "}
            {data && data.channels.length > 0
              ? `The map shows ${fmt(data.channels.length)} of the ${fmt(data.overallStats.totalChannels)} analysed channels${source === "live" ? ` in the ${dataWindow}-day window` : ""}, with at most ${LINK_CAP} links each.`
              : `The map shows at most 1,000 of the analysed channels, with at most ${LINK_CAP} links each.`}
          </li>
        </ul>
      </Row>

      <Row top={32} gap={10}>
        <Section id="stack" n={9} title="Stack" />
        <dl className="facts" style={{ gridTemplateColumns: "150px minmax(0, 1fr)", gap: "3px 16px", fontSize: 17 }}>
          <dt>pipeline</dt>
          <dd style={{ font: "inherit" }}>Python 3.11, TwitchIO (EventSub), pandas, PyArrow, NetworkX, python-louvain</dd>
          <dt>infrastructure</dt>
          <dd style={{ font: "inherit" }}>AWS: scheduled Fargate tasks, S3, DynamoDB lease, CloudFront</dd>
          <dt>this site</dt>
          <dd style={{ font: "inherit" }}>React, TypeScript, Vite. Every data file is validated before it's drawn.</dd>
          <dt>tests</dt>
          <dd style={{ font: "inherit" }}>pytest, no network or credentials needed</dd>
        </dl>
      </Row>

      <Row top={32} gap={10}>
        <Section id="faq" n={10} title="FAQ" />
        <dl style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <dt style={{ fontWeight: 600 }}>How often does it update?</dt>
            <dd className="pretty">
              Surveys run three times a day. Analysis runs once a day at 01:00 ET, and the map is rebuilt
              from it.
            </dd>
          </div>
          <div>
            <dt style={{ fontWeight: 600 }}>Can I run it myself?</dt>
            <dd className="pretty">
              Yes. The synthetic-data path needs no Twitch account. Collecting real data needs a Twitch app
              and a bot token with <code>user:read:chat</code>.
            </dd>
          </div>
          <div>
            <dt style={{ fontWeight: 600 }}>Is this affiliated with Twitch?</dt>
            <dd>No. It's an independent project.</dd>
          </div>
        </dl>
      </Row>

      <Row top={32} gap={10}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 16, color: "var(--caption)" }}>
          <h2 style={{ fontWeight: 600, fontSize: 18, lineHeight: 1.3, color: "var(--ink)" }}>
            A note on how this was built
          </h2>
          <p className="pretty">
            I used AI coding assistants for implementation, debugging and documentation. I decided the
            research question, what data is collected and published, the metrics, and the analysis rules. I
            also checked the results and decided which changes went in. Each decision is recorded next to
            the code that implements it.
          </p>
        </div>
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

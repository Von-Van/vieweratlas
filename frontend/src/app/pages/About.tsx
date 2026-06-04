import { Github, ArrowRight, MessageSquare, GitMerge, Map, Shield, Code2, Cpu, BarChart2, Database } from "lucide-react";
import { Link } from "react-router";

const PIPELINE = [
  {
    step: "01",
    icon: <MessageSquare size={24} style={{ color: "#9147FF" }} />,
    title: "Collect",
    subtitle: "Live Twitch Chat Sampling",
    color: "#9147FF",
    desc: "ViewerAtlas connects to Twitch's IRC chat interface and samples configured channel batches. It records which usernames are observed in each collection window without storing message text, then builds a co-occurrence matrix of viewer presence.",
    details: [
      "IRC bot connects to configured channel batches",
      "Records unique chatters observed per collection window",
      "Writes batched Parquet presence snapshots",
      "Supports local storage or encrypted S3 objects",
    ],
  },
  {
    step: "02",
    icon: <GitMerge size={24} style={{ color: "#00E5CC" }} />,
    title: "Analyze",
    subtitle: "Build Overlap Graph + Run Community Detection",
    color: "#00E5CC",
    desc: "From the co-presence data, we construct a weighted channel-overlap graph where each node is a Twitch channel and each edge weight represents the number of shared viewers between two channels. We then apply the Louvain modularity optimization algorithm to identify natural community clusters.",
    details: [
      "Constructs bipartite viewer-channel graph",
      "Projects to channel-channel overlap graph",
      "Applies Louvain algorithm for community detection",
      "Computes modularity score (Q) as quality metric",
    ],
  },
  {
    step: "03",
    icon: <Map size={24} style={{ color: "#FF7B00" }} />,
    title: "Visualize",
    subtitle: "Explore the Twitch Community Map",
    color: "#FF7B00",
    desc: "The resulting graph is visualized using a force-directed layout where node size reflects viewer count and edge thickness represents shared viewer overlap. Communities are color-coded, enabling intuitive exploration of the Twitch ecosystem.",
    details: [
      "Force-directed layout with D3-style physics",
      "Node radius scales with viewer count",
      "Edge weight maps to visual thickness",
      "Interactive zoom, pan, and click-to-inspect",
    ],
  },
];

const TECH_STACK = [
  { icon: <Code2 size={16} style={{ color: "#9147FF" }} />, name: "Python 3.11", desc: "Core data pipeline" },
  { icon: <MessageSquare size={16} style={{ color: "#00E5CC" }} />, name: "TwitchIO", desc: "IRC chat client" },
  { icon: <Cpu size={16} style={{ color: "#FF7B00" }} />, name: "NetworkX", desc: "Graph construction" },
  { icon: <GitMerge size={16} style={{ color: "#1DB954" }} />, name: "python-louvain", desc: "Community detection" },
  { icon: <Database size={16} style={{ color: "#FF4D6D" }} />, name: "PyArrow / Parquet", desc: "Columnar snapshots" },
  { icon: <BarChart2 size={16} style={{ color: "#FFD700" }} />, name: "React / Vite", desc: "Interactive portfolio UI" },
];

const FAQ = [
  {
    q: "Does ViewerAtlas store personal user data?",
    a: "ViewerAtlas stores observed Twitch usernames in raw presence snapshots. By default, downloaded VOD message content is discarded after presence extraction, though operators can explicitly opt in to retain raw VOD artifacts. Handles are pseudonymous identifiers and may still be personal data.",
  },
  {
    q: "How often is the data updated?",
    a: "Collection and analysis schedules are configurable. The current pipeline prevents duplicate live collection for the same channel on the same UTC day, while analysis can run on a separate schedule.",
  },
  {
    q: "How does the Louvain algorithm work?",
    a: "The Louvain algorithm optimizes modularity — a measure of how well a graph divides into communities. It iteratively reassigns nodes to communities that maximize the gain in modularity, then merges communities into super-nodes and repeats.",
  },
  {
    q: "Can I run this locally?",
    a: "Yes! The full source is on GitHub. You'll need a Twitch API token and a list of channels to track. The README has full setup instructions.",
  },
  {
    q: "Is this affiliated with Twitch?",
    a: "No. ViewerAtlas is an independent, open-source research project. It uses publicly available Twitch chat data via the IRC interface. It is not affiliated with, endorsed by, or sponsored by Twitch Interactive, Inc.",
  },
];

export function About() {
  return (
    <div
      style={{
        background: "#0E0E10",
        color: "#EFEFF1",
        minHeight: "100vh",
        fontFamily: "'Space Grotesk', sans-serif",
        paddingBottom: 80,
      }}
    >
      {/* Page header */}
      <div
        className="py-16 px-6 text-center"
        style={{
          background: "linear-gradient(180deg, rgba(145,71,255,0.07) 0%, #0E0E10 100%)",
          borderBottom: "1px solid #2A2A2E",
        }}
      >
        <div className="max-w-3xl mx-auto">
          <div
            className="inline-block px-3 py-1 rounded-full text-xs mb-4"
            style={{
              background: "rgba(145, 71, 255, 0.12)",
              border: "1px solid rgba(145, 71, 255, 0.3)",
              color: "#9147FF",
              fontWeight: 600,
              letterSpacing: "0.05em",
            }}
          >
            HOW IT WORKS
          </div>
          <h1
            style={{
              color: "#EFEFF1",
              fontSize: "clamp(1.75rem, 4vw, 3rem)",
              fontWeight: 800,
              letterSpacing: "-0.03em",
              lineHeight: 1.2,
              marginBottom: 12,
            }}
          >
            From Live Chat to Community Map
          </h1>
          <p style={{ color: "#848494", fontSize: 16, lineHeight: 1.7 }}>
            A transparent look at how ViewerAtlas collects data, builds the graph, and surfaces communities you never knew existed.
          </p>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-4 sm:px-6 mt-16">
        {/* Pipeline steps */}
        <div className="relative">
          {/* Vertical line */}
          <div
            className="absolute left-8 top-8 bottom-8 w-px hidden md:block"
            style={{ background: "linear-gradient(to bottom, #9147FF, #00E5CC, #FF7B00)" }}
          />

          <div className="flex flex-col gap-12">
            {PIPELINE.map((step) => (
              <div key={step.step} className="relative flex gap-8">
                {/* Step circle */}
                <div
                  className="hidden md:flex flex-shrink-0 items-center justify-center rounded-full z-10"
                  style={{
                    width: 64,
                    height: 64,
                    background: "#18181B",
                    border: `2px solid ${step.color}`,
                    boxShadow: `0 0 20px ${step.color}44`,
                  }}
                >
                  {step.icon}
                </div>

                {/* Content */}
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-1">
                    <span
                      style={{
                        color: step.color,
                        fontWeight: 800,
                        fontSize: 12,
                        letterSpacing: "0.08em",
                      }}
                    >
                      STEP {step.step}
                    </span>
                    <div className="md:hidden">{step.icon}</div>
                  </div>
                  <h2
                    style={{
                      color: "#EFEFF1",
                      fontSize: "clamp(1.25rem, 2.5vw, 1.75rem)",
                      fontWeight: 700,
                      letterSpacing: "-0.02em",
                      marginBottom: 4,
                    }}
                  >
                    {step.title}
                  </h2>
                  <div style={{ color: step.color, fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                    {step.subtitle}
                  </div>
                  <p style={{ color: "#848494", fontSize: 15, lineHeight: 1.7, marginBottom: 16 }}>
                    {step.desc}
                  </p>

                  <div
                    className="p-4 rounded-xl"
                    style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
                  >
                    <ul className="flex flex-col gap-2">
                      {step.details.map((d, i) => (
                        <li key={i} className="flex items-start gap-2.5">
                          <div
                            className="rounded-full flex-shrink-0 mt-1.5"
                            style={{ width: 6, height: 6, background: step.color }}
                          />
                          <span style={{ color: "#EFEFF1", fontSize: 14, lineHeight: 1.5 }}>{d}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Tech stack */}
        <div
          className="mt-16 p-8 rounded-2xl"
          style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
        >
          <h2
            style={{
              color: "#EFEFF1",
              fontWeight: 700,
              fontSize: 20,
              letterSpacing: "-0.02em",
              marginBottom: 4,
            }}
          >
            Tech Stack
          </h2>
          <p style={{ color: "#848494", fontSize: 14, marginBottom: 20 }}>
            Built with an open-source Python data stack and a React visualization layer.
          </p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {TECH_STACK.map((tech) => (
              <div
                key={tech.name}
                className="flex items-center gap-3 px-4 py-3 rounded-xl"
                style={{ background: "#0E0E10", border: "1px solid #2A2A2E" }}
              >
                {tech.icon}
                <div>
                  <div style={{ color: "#EFEFF1", fontWeight: 600, fontSize: 14 }}>{tech.name}</div>
                  <div style={{ color: "#848494", fontSize: 12 }}>{tech.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Privacy */}
        <div
          className="mt-8 p-8 rounded-2xl"
          style={{
            background: "rgba(29, 185, 84, 0.05)",
            border: "1px solid rgba(29, 185, 84, 0.25)",
          }}
        >
          <div className="flex items-start gap-4">
            <div
              className="rounded-xl flex items-center justify-center flex-shrink-0"
              style={{
                width: 44,
                height: 44,
                background: "rgba(29, 185, 84, 0.15)",
                border: "1px solid rgba(29, 185, 84, 0.3)",
              }}
            >
              <Shield size={20} style={{ color: "#1DB954" }} />
            </div>
            <div>
              <h2
                style={{
                  color: "#1DB954",
                  fontWeight: 700,
                  fontSize: 18,
                  letterSpacing: "-0.01em",
                  marginBottom: 8,
                }}
              >
                Privacy-Conscious Design
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <p style={{ color: "#EFEFF1", fontSize: 14, lineHeight: 1.7 }}>
                    ViewerAtlas is designed to be privacy-conscious by default:
                  </p>
                  <ul className="mt-3 flex flex-col gap-2">
                    {[
                      "Message content is not retained by default",
                      "Raw presence data is kept private by default",
                      "The public frontend receives channel-level aggregates",
                      "No account details or profile data collected",
                      "Example AWS retention controls expire raw data",
                    ].map((item) => (
                      <li key={item} className="flex items-start gap-2">
                        <div
                          className="rounded-full flex-shrink-0 mt-1.5"
                          style={{ width: 5, height: 5, background: "#1DB954" }}
                        />
                        <span style={{ color: "#848494", fontSize: 13 }}>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p style={{ color: "#848494", fontSize: 14, lineHeight: 1.7 }}>
                    The pipeline tracks whether a username appeared in a channel during a sampling window, not what they said. Raw handles remain sensitive operational data; the portfolio frontend is designed to expose only aggregated channel-level statistics.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* FAQ */}
        <div className="mt-12">
          <h2
            style={{
              color: "#EFEFF1",
              fontWeight: 700,
              fontSize: 22,
              letterSpacing: "-0.02em",
              marginBottom: 20,
            }}
          >
            Frequently Asked Questions
          </h2>
          <div className="flex flex-col gap-4">
            {FAQ.map((item, i) => (
              <div
                key={i}
                className="p-5 rounded-xl"
                style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
              >
                <div
                  style={{ color: "#EFEFF1", fontWeight: 600, fontSize: 15, marginBottom: 8 }}
                >
                  {item.q}
                </div>
                <p style={{ color: "#848494", fontSize: 14, lineHeight: 1.7 }}>{item.a}</p>
              </div>
            ))}
          </div>
        </div>

        {/* GitHub CTA */}
        <div
          className="mt-12 p-8 rounded-2xl text-center"
          style={{
            background: "linear-gradient(135deg, rgba(145,71,255,0.12) 0%, rgba(0,229,204,0.06) 100%)",
            border: "1px solid rgba(145, 71, 255, 0.25)",
          }}
        >
          <h2
            style={{
              color: "#EFEFF1",
              fontWeight: 800,
              fontSize: 22,
              letterSpacing: "-0.02em",
              marginBottom: 8,
            }}
          >
            It's fully open source
          </h2>
          <p style={{ color: "#848494", fontSize: 15, lineHeight: 1.7, marginBottom: 24, maxWidth: 480, margin: "0 auto 24px" }}>
            Fork it, deploy it, run it on your own channel list. All data pipeline code, graph processing, and this frontend are MIT-licensed.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-4">
            <a
              href="https://github.com/Von-Van/vieweratlas"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-6 py-3 rounded-xl transition-all"
              style={{
                background: "#9147FF",
                color: "#fff",
                textDecoration: "none",
                fontWeight: 600,
                fontSize: 15,
              }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "#7c2fd4")}
              onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "#9147FF")}
            >
              <Github size={16} />
              View on GitHub
            </a>
            <Link
              to="/map"
              className="flex items-center gap-2 px-6 py-3 rounded-xl transition-all"
              style={{
                border: "1px solid #2A2A2E",
                color: "#EFEFF1",
                textDecoration: "none",
                fontWeight: 600,
                fontSize: 15,
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = "#00E5CC";
                (e.currentTarget as HTMLElement).style.color = "#00E5CC";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = "#2A2A2E";
                (e.currentTarget as HTMLElement).style.color = "#EFEFF1";
              }}
            >
              Explore the Map <ArrowRight size={14} />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

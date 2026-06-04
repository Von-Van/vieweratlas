import { Link } from "react-router";
import {
  ArrowRight,
  Zap,
  Users,
  GitBranch,
  BarChart3,
  Github,
  ChevronRight,
  Activity,
} from "lucide-react";
import { HeroGraph } from "../components/HeroGraph";
import { useAtlasData } from "../data/useAtlasData";
import { LoadingSkeleton } from "../components/LoadingSkeleton";

const FEATURES = [
  {
    icon: <Activity size={20} style={{ color: "#9147FF" }} />,
    title: "Live Chat Sampling",
    desc: "Samples configured Twitch channel batches and records unique chatters observed during each collection window.",
    accent: "#9147FF",
  },
  {
    icon: <GitBranch size={20} style={{ color: "#00E5CC" }} />,
    title: "Overlap Graph Engine",
    desc: "Builds a weighted bipartite graph from viewer co-presence data, calculating edge weights from shared audience counts.",
    accent: "#00E5CC",
  },
  {
    icon: <Users size={20} style={{ color: "#FF7B00" }} />,
    title: "Louvain Community Detection",
    desc: "Runs the Louvain modularity optimization algorithm to automatically detect and label distinct viewer communities.",
    accent: "#FF7B00",
  },
  {
    icon: <BarChart3 size={20} style={{ color: "#1DB954" }} />,
    title: "Interactive Visualization",
    desc: "Renders the community graph with force-directed layout, zoom/pan controls, and filterable community overlays.",
    accent: "#1DB954",
  },
];

const compactNumber = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function StatCard({
  value,
  label,
  accent,
}: {
  value: string;
  label: string;
  accent: string;
}) {
  return (
    <div
      className="flex flex-col items-center justify-center p-6 rounded-xl"
      style={{
        background: "#18181B",
        border: "1px solid #2A2A2E",
      }}
    >
      <div
        className="text-3xl font-bold mb-1"
        style={{
          color: accent,
          fontFamily: "'Space Grotesk', sans-serif",
          fontWeight: 700,
        }}
      >
        {value}
      </div>
      <div
        style={{
          color: "#848494",
          fontSize: 13,
          textAlign: "center",
        }}
      >
        {label}
      </div>
    </div>
  );
}

export function Landing() {
  const { data, loading } = useAtlasData();

  if (loading || !data) return <LoadingSkeleton />;

  const { communities, overallStats } = data;

  return (
    <div
      style={{
        background: "#0E0E10",
        color: "#EFEFF1",
        fontFamily: "'Space Grotesk', sans-serif",
      }}
    >
      {/* Hero */}
      <section
        className="relative overflow-hidden"
        style={{ minHeight: "calc(100vh - 64px)" }}
      >
        {/* Background graph */}
        <div className="absolute inset-0 z-0">
          <HeroGraph className="w-full h-full" />
          <div
            className="absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse at 50% 50%, rgba(14,14,16,0.4) 0%, rgba(14,14,16,0.85) 70%, #0E0E10 100%)",
            }}
          />
        </div>

        {/* Hero content */}
        <div
          className="relative z-10 flex flex-col items-center justify-center text-center px-4 py-24"
          style={{ minHeight: "calc(100vh - 64px)" }}
        >
          {/* Badge */}
          <div
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs mb-6"
            style={{
              background: "rgba(145, 71, 255, 0.15)",
              border: "1px solid rgba(145, 71, 255, 0.35)",
              color: "#9147FF",
              fontWeight: 600,
              letterSpacing: "0.05em",
            }}
          >
            <span
              className="inline-block rounded-full"
              style={{
                width: 6,
                height: 6,
                background: "#9147FF",
                boxShadow: "0 0 6px #9147FF",
              }}
            />
            OPEN SOURCE · TWITCH ANALYTICS
          </div>

          <h1
            className="mb-4"
            style={{
              fontSize: "clamp(2.5rem, 6vw, 5rem)",
              fontWeight: 800,
              lineHeight: 1.1,
              letterSpacing: "-0.03em",
              color: "#EFEFF1",
              maxWidth: 800,
            }}
          >
            Map the{" "}
            <span
              style={{
                background:
                  "linear-gradient(90deg, #9147FF, #00E5CC)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              Twitch Universe
            </span>
          </h1>

          <p
            className="mb-10 max-w-2xl"
            style={{
              color: "#848494",
              fontSize: "clamp(1rem, 2vw, 1.2rem)",
              lineHeight: 1.7,
            }}
          >
            ViewerAtlas discovers hidden communities by tracking
            shared viewers between channels. Powered by live
            chat sampling, graph theory, and the Louvain
            algorithm.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-4">
            <Link
              to="/map"
              className="flex items-center gap-2 px-6 py-3 rounded-xl transition-all"
              style={{
                background: "#9147FF",
                color: "#fff",
                textDecoration: "none",
                fontWeight: 600,
                fontSize: 15,
              }}
              onMouseEnter={(e) =>
                (e.currentTarget.style.background = "#7c2fd4")
              }
              onMouseLeave={(e) =>
                (e.currentTarget.style.background = "#9147FF")
              }
            >
              Explore the Map
              <ArrowRight size={16} />
            </Link>
            <a
              href="https://github.com/Von-Van/vieweratlas"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-6 py-3 rounded-xl transition-all"
              style={{
                background: "transparent",
                border: "1px solid #2A2A2E",
                color: "#EFEFF1",
                textDecoration: "none",
                fontWeight: 600,
                fontSize: 15,
              }}
              onMouseEnter={(e) => {
                (
                  e.currentTarget as HTMLElement
                ).style.borderColor = "#9147FF";
                (e.currentTarget as HTMLElement).style.color =
                  "#9147FF";
              }}
              onMouseLeave={(e) => {
                (
                  e.currentTarget as HTMLElement
                ).style.borderColor = "#2A2A2E";
                (e.currentTarget as HTMLElement).style.color =
                  "#EFEFF1";
              }}
            >
              <Github size={16} />
              View on GitHub
            </a>
          </div>

          {/* Scroll indicator */}
          <div
            className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-1"
            style={{ color: "#848494" }}
          >
            <span
              style={{ fontSize: 11, letterSpacing: "0.1em" }}
            >
              SCROLL TO EXPLORE
            </span>
            <div
              className="w-0.5 h-8 rounded-full"
              style={{
                background:
                  "linear-gradient(to bottom, #9147FF, transparent)",
                animation: "pulse 2s infinite",
              }}
            />
          </div>
        </div>
      </section>

      {/* Stats strip */}
      <section
        className="py-6 px-4"
        style={{
          background: "#18181B",
          borderTop: "1px solid #2A2A2E",
          borderBottom: "1px solid #2A2A2E",
        }}
      >
        <div className="max-w-7xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-4">
          <div
            className="text-center px-4"
            style={{ borderRight: "1px solid #2A2A2E" }}
          >
            <div
              style={{
                color: "#9147FF",
                fontWeight: 700,
                fontSize: 28,
              }}
            >
              {overallStats.totalChannels.toLocaleString()}
            </div>
            <div
              style={{
                color: "#848494",
                fontSize: 12,
                marginTop: 2,
              }}
            >
              Channels Tracked
            </div>
          </div>
          <div
            className="text-center px-4"
            style={{ borderRight: "1px solid #2A2A2E" }}
          >
            <div
              style={{
                color: "#00E5CC",
                fontWeight: 700,
                fontSize: 28,
              }}
            >
              {compactNumber.format(overallStats.totalViewers)}
            </div>
            <div
              style={{
                color: "#848494",
                fontSize: 12,
                marginTop: 2,
              }}
            >
              Unique Viewers
            </div>
          </div>
          <div
            className="text-center px-4"
            style={{ borderRight: "1px solid #2A2A2E" }}
          >
            <div
              style={{
                color: "#FF7B00",
                fontWeight: 700,
                fontSize: 28,
              }}
            >
              {overallStats.communitiesDetected}
            </div>
            <div
              style={{
                color: "#848494",
                fontSize: 12,
                marginTop: 2,
              }}
            >
              Communities Found
            </div>
          </div>
          <div className="text-center px-4">
            <div
              style={{
                color: "#1DB954",
                fontWeight: 700,
                fontSize: 28,
              }}
            >
              {overallStats.modularityScore}
            </div>
            <div
              style={{
                color: "#848494",
                fontSize: 12,
                marginTop: 2,
              }}
            >
              Modularity Score
            </div>
          </div>
        </div>
      </section>

      {/* Communities preview */}
      <section className="py-20 px-4">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-12">
            <div
              className="inline-block px-3 py-1 rounded-full text-xs mb-3"
              style={{
                background: "rgba(0, 229, 204, 0.1)",
                border: "1px solid rgba(0, 229, 204, 0.25)",
                color: "#00E5CC",
                fontWeight: 600,
                letterSpacing: "0.05em",
              }}
            >
              DETECTED COMMUNITIES
            </div>
            <h2
              style={{
                color: "#EFEFF1",
                fontSize: "clamp(1.5rem, 3vw, 2.25rem)",
                fontWeight: 700,
                letterSpacing: "-0.02em",
                lineHeight: 1.3,
              }}
            >
              Discover Hidden Viewer Clusters
            </h2>
            <p
              style={{
                color: "#848494",
                fontSize: 15,
                marginTop: 8,
                maxWidth: 500,
                margin: "8px auto 0",
              }}
            >
              Louvain community detection reveals natural
              groupings you won't find anywhere else.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {communities.map((comm) => (
              <Link
                key={comm.id}
                to="/map"
                style={{ textDecoration: "none" }}
              >
                <div
                  className="p-5 rounded-xl transition-all group cursor-pointer"
                  style={{
                    background: "#18181B",
                    border: "1px solid #2A2A2E",
                  }}
                  onMouseEnter={(e) => {
                    (
                      e.currentTarget as HTMLElement
                    ).style.borderColor = comm.color + "55";
                    (
                      e.currentTarget as HTMLElement
                    ).style.background = "#1e1e22";
                  }}
                  onMouseLeave={(e) => {
                    (
                      e.currentTarget as HTMLElement
                    ).style.borderColor = "#2A2A2E";
                    (
                      e.currentTarget as HTMLElement
                    ).style.background = "#18181B";
                  }}
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <div
                        className="w-3 h-3 rounded-full flex-shrink-0"
                        style={{
                          background: comm.color,
                          boxShadow: `0 0 8px ${comm.color}88`,
                        }}
                      />
                      <span
                        style={{
                          color: "#EFEFF1",
                          fontWeight: 600,
                          fontSize: 15,
                        }}
                      >
                        {comm.label}
                      </span>
                    </div>
                    <ChevronRight
                      size={16}
                      style={{ color: "#848494" }}
                    />
                  </div>
                  <p
                    style={{
                      color: "#848494",
                      fontSize: 13,
                      lineHeight: 1.6,
                      marginBottom: 12,
                    }}
                  >
                    {comm.description}
                  </p>
                  <div className="flex items-center gap-4">
                    <div>
                      <div
                        style={{
                          color: comm.color,
                          fontWeight: 700,
                          fontSize: 18,
                        }}
                      >
                        {comm.nodeCount}
                      </div>
                      <div
                        style={{
                          color: "#848494",
                          fontSize: 11,
                        }}
                      >
                        channels
                      </div>
                    </div>
                    <div
                      className="flex-1 h-1 rounded-full overflow-hidden"
                      style={{ background: "#2A2A2E" }}
                    >
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${(comm.nodeCount / 120) * 100}%`,
                          background: comm.color,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section
        className="py-20 px-4"
        style={{
          background: "#18181B",
          borderTop: "1px solid #2A2A2E",
          borderBottom: "1px solid #2A2A2E",
        }}
      >
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-12">
            <div
              className="inline-block px-3 py-1 rounded-full text-xs mb-3"
              style={{
                background: "rgba(145, 71, 255, 0.1)",
                border: "1px solid rgba(145, 71, 255, 0.25)",
                color: "#9147FF",
                fontWeight: 600,
                letterSpacing: "0.05em",
              }}
            >
              HOW IT WORKS
            </div>
            <h2
              style={{
                color: "#EFEFF1",
                fontSize: "clamp(1.5rem, 3vw, 2.25rem)",
                fontWeight: 700,
                letterSpacing: "-0.02em",
              }}
            >
              Data Pipeline Overview
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {FEATURES.map((f, i) => (
              <div
                key={i}
                className="p-6 rounded-xl"
                style={{
                  background: "#0E0E10",
                  border: "1px solid #2A2A2E",
                }}
              >
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center mb-4"
                  style={{
                    background: f.accent + "18",
                    border: `1px solid ${f.accent}33`,
                  }}
                >
                  {f.icon}
                </div>
                <div
                  style={{
                    color: "#EFEFF1",
                    fontWeight: 600,
                    fontSize: 15,
                    marginBottom: 8,
                  }}
                >
                  <span
                    style={{
                      color: f.accent,
                      fontWeight: 700,
                      fontSize: 11,
                      letterSpacing: "0.08em",
                      display: "block",
                      marginBottom: 4,
                    }}
                  >
                    STEP {i + 1}
                  </span>
                  {f.title}
                </div>
                <p
                  style={{
                    color: "#848494",
                    fontSize: 13,
                    lineHeight: 1.7,
                  }}
                >
                  {f.desc}
                </p>
              </div>
            ))}
          </div>

          <div className="text-center mt-10">
            <Link
              to="/about"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl transition-all"
              style={{
                border: "1px solid #2A2A2E",
                color: "#848494",
                textDecoration: "none",
                fontWeight: 500,
                fontSize: 14,
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.color =
                  "#9147FF";
                (
                  e.currentTarget as HTMLElement
                ).style.borderColor = "#9147FF";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.color =
                  "#848494";
                (
                  e.currentTarget as HTMLElement
                ).style.borderColor = "#2A2A2E";
              }}
            >
              Full Technical Overview <ArrowRight size={14} />
            </Link>
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-24 px-4 text-center">
        <div className="max-w-2xl mx-auto">
          <div
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs mb-6"
            style={{
              background: "rgba(145, 71, 255, 0.15)",
              border: "1px solid rgba(145, 71, 255, 0.35)",
              color: "#9147FF",
              fontWeight: 600,
            }}
          >
            <Zap size={12} />
            LIVE DATA — UPDATED DAILY
          </div>
          <h2
            style={{
              color: "#EFEFF1",
              fontSize: "clamp(1.75rem, 4vw, 3rem)",
              fontWeight: 800,
              letterSpacing: "-0.03em",
              lineHeight: 1.2,
              marginBottom: 16,
            }}
          >
            Ready to explore the map?
          </h2>
          <p
            style={{
              color: "#848494",
              fontSize: 16,
              lineHeight: 1.7,
              marginBottom: 32,
            }}
          >
            Dive into the interactive community graph, discover
            overlapping audiences, and see which channels share
            the most viewers.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-4">
            <Link
              to="/map"
              className="flex items-center gap-2 px-8 py-4 rounded-xl transition-all"
              style={{
                background:
                  "linear-gradient(135deg, #9147FF, #7c2fd4)",
                color: "#fff",
                textDecoration: "none",
                fontWeight: 700,
                fontSize: 16,
                boxShadow: "0 0 30px rgba(145, 71, 255, 0.35)",
              }}
              onMouseEnter={(e) => {
                (
                  e.currentTarget as HTMLElement
                ).style.boxShadow =
                  "0 0 40px rgba(145, 71, 255, 0.55)";
              }}
              onMouseLeave={(e) => {
                (
                  e.currentTarget as HTMLElement
                ).style.boxShadow =
                  "0 0 30px rgba(145, 71, 255, 0.35)";
              }}
            >
              Explore the Map <ArrowRight size={18} />
            </Link>
            <Link
              to="/stats"
              className="flex items-center gap-2 px-6 py-4 rounded-xl transition-all"
              style={{
                border: "1px solid #2A2A2E",
                color: "#EFEFF1",
                textDecoration: "none",
                fontWeight: 600,
                fontSize: 16,
              }}
              onMouseEnter={(e) => {
                (
                  e.currentTarget as HTMLElement
                ).style.borderColor = "#00E5CC";
                (e.currentTarget as HTMLElement).style.color =
                  "#00E5CC";
              }}
              onMouseLeave={(e) => {
                (
                  e.currentTarget as HTMLElement
                ).style.borderColor = "#2A2A2E";
                (e.currentTarget as HTMLElement).style.color =
                  "#EFEFF1";
              }}
            >
              <BarChart3 size={16} />
              View Statistics
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}

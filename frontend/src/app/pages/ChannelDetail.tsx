import { useParams, Link } from "react-router";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  CartesianGrid,
} from "recharts";
import { ArrowLeft, Users, GitBranch, TrendingUp, Globe, ExternalLink } from "lucide-react";
import { useAtlasData, ANALYSIS_WINDOWS } from "../data/useAtlasData";
import { LoadingSkeleton } from "../components/LoadingSkeleton";

function getInitials(name: string) {
  return name.slice(0, 2).toUpperCase();
}

const CustomTooltipBar = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div
        style={{
          background: "#18181B",
          border: "1px solid #2A2A2E",
          borderRadius: 8,
          padding: "8px 12px",
          fontFamily: "'Space Grotesk', sans-serif",
        }}
      >
        <div style={{ color: "#EFEFF1", fontWeight: 600, fontSize: 13 }}>{label}</div>
        <div style={{ color: "#00E5CC", fontSize: 12 }}>
          {payload[0].value.toLocaleString()} shared chatters
        </div>
      </div>
    );
  }
  return null;
};

const CustomTooltipLine = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div
        style={{
          background: "#18181B",
          border: "1px solid #2A2A2E",
          borderRadius: 8,
          padding: "8px 12px",
          fontFamily: "'Space Grotesk', sans-serif",
        }}
      >
        <div style={{ color: "#848494", fontSize: 11 }}>{label}</div>
        <div style={{ color: "#9147FF", fontWeight: 700, fontSize: 14 }}>
          {payload[0].value.toLocaleString()} viewers
        </div>
      </div>
    );
  }
  return null;
};

export function ChannelDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, loading, window: activeWindow, setWindow, windowAvailable, switching } = useAtlasData();

  if (loading || !data) return <LoadingSkeleton />;

  const { channels, communities } = data;
  const channel = channels.find((c) => c.id === id);
  const community = channel ? communities.find((c) => c.id === channel.communityId) : null;
  const color = community?.color ?? "#9147FF";

  if (!channel || !community) {
    // A narrow window samples fewer channels, so a link that worked at 90 days
    // can miss at 14. Offer the wider window instead of a dead end.
    const widestWindow = ANALYSIS_WINDOWS[ANALYSIS_WINDOWS.length - 1];
    const canWiden = windowAvailable && activeWindow !== widestWindow;

    return (
      <div
        className="flex flex-col items-center justify-center py-40"
        style={{ color: "#848494", fontFamily: "'Space Grotesk', sans-serif" }}
      >
        <div style={{ fontSize: 48, marginBottom: 16 }}>😕</div>
        <div style={{ color: "#EFEFF1", fontSize: 20, fontWeight: 700, marginBottom: 8 }}>
          {canWiden ? "Not in this window" : "Channel not found"}
        </div>
        <p style={{ marginBottom: 24, maxWidth: 420, textAlign: "center", lineHeight: 1.6 }}>
          {canWiden
            ? `This channel wasn't sampled often enough to appear in the last ${activeWindow} days. A wider window may include it.`
            : "This channel isn't in the current dataset."}
        </p>
        <div className="flex items-center gap-3">
          {canWiden && (
            <button
              onClick={() => setWindow(widestWindow)}
              disabled={switching}
              className="px-5 py-2.5 rounded-xl"
              style={{
                background: "#9147FF",
                color: "#fff",
                border: "none",
                cursor: switching ? "wait" : "pointer",
                fontFamily: "'Space Grotesk', sans-serif",
                fontWeight: 600,
              }}
            >
              {switching ? "Loading…" : `Try ${widestWindow} days`}
            </button>
          )}
          <Link
            to="/map"
            className="px-5 py-2.5 rounded-xl"
            style={{
              background: canWiden ? "transparent" : "#9147FF",
              border: canWiden ? "1px solid #2A2A2E" : "none",
              color: canWiden ? "#848494" : "#fff",
              textDecoration: "none",
              fontWeight: 600,
            }}
          >
            Back to Map
          </Link>
        </div>
      </div>
    );
  }

  // Related channels (same community, different)
  const relatedChannels = channels
    .filter((c) => c.communityId === channel.communityId && c.id !== channel.id)
    .slice(0, 4);

  const overlapData = channel.topOverlaps.map((ov) => ({
    name: ov.channelName,
    shared: ov.shared,
  }));

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
      {/* Header banner */}
      <div
        className="relative py-12 px-6"
        style={{
          background: `linear-gradient(135deg, ${color}18 0%, #0E0E10 60%)`,
          borderBottom: "1px solid #2A2A2E",
        }}
      >
        {/* Glow */}
        <div
          className="absolute top-0 left-0 w-80 h-40 rounded-full blur-3xl opacity-20 pointer-events-none"
          style={{ background: color }}
        />

        <div className="max-w-6xl mx-auto relative z-10">
          <Link
            to="/map"
            className="inline-flex items-center gap-2 mb-6 text-sm transition-all"
            style={{ color: "#848494", textDecoration: "none", fontWeight: 500 }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#EFEFF1")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#848494")}
          >
            <ArrowLeft size={14} />
            Back to Community Map
          </Link>

          <div className="flex flex-col sm:flex-row items-start sm:items-center gap-6">
            {/* Avatar */}
            <div
              className="flex items-center justify-center rounded-2xl flex-shrink-0"
              style={{
                width: 80,
                height: 80,
                background: `linear-gradient(135deg, ${color}, ${color}88)`,
                fontSize: 28,
                fontWeight: 800,
                color: "#fff",
                boxShadow: `0 0 30px ${color}55`,
              }}
            >
              {getInitials(channel.displayName)}
            </div>

            <div className="flex-1">
              <div className="flex flex-wrap items-center gap-3 mb-2">
                <h1
                  style={{
                    color: "#EFEFF1",
                    fontSize: "clamp(1.5rem, 3vw, 2rem)",
                    fontWeight: 800,
                    letterSpacing: "-0.02em",
                    lineHeight: 1.2,
                  }}
                >
                  {channel.displayName}
                </h1>
                <div
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs"
                  style={{
                    background: color + "18",
                    border: `1px solid ${color}44`,
                    color,
                    fontWeight: 600,
                  }}
                >
                  <div className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
                  {community.label}
                </div>
                <div
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs"
                  style={{
                    background: "rgba(0, 229, 204, 0.1)",
                    border: "1px solid rgba(0, 229, 204, 0.25)",
                    color: "#00E5CC",
                    fontWeight: 600,
                  }}
                >
                  <Globe size={11} />
                  {channel.language}
                </div>
              </div>

              <p style={{ color: "#848494", fontSize: 14, lineHeight: 1.6, marginBottom: 12, maxWidth: 600 }}>
                {channel.description}
              </p>

              <div style={{ color: "#9147FF", fontSize: 13, fontWeight: 600 }}>
                Currently playing: {channel.game}
              </div>
            </div>

            <a
              href={`https://twitch.tv/${channel.name}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl flex-shrink-0 transition-all"
              style={{
                background: color,
                color: "#fff",
                textDecoration: "none",
                fontWeight: 600,
                fontSize: 14,
              }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.opacity = "0.85")}
              onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.opacity = "1")}
            >
              <ExternalLink size={14} />
              Twitch.tv
            </a>
          </div>

          {/* Stats row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-8">
            {[
              {
                icon: <Users size={16} style={{ color }} />,
                value: channel.viewers.toLocaleString(),
                label: "Live Viewers",
                accentColor: color,
              },
              {
                icon: <GitBranch size={16} style={{ color: "#00E5CC" }} />,
                value: channel.edgeCount,
                label: "Connections",
                accentColor: "#00E5CC",
              },
              {
                icon: <TrendingUp size={16} style={{ color: "#1DB954" }} />,
                value: channel.modularityScore,
                label: "Modularity Score",
                accentColor: "#1DB954",
              },
              {
                icon: <Globe size={16} style={{ color: "#FF7B00" }} />,
                value: channel.topOverlaps.length,
                label: "Top Overlaps",
                accentColor: "#FF7B00",
              },
            ].map((stat, i) => (
              <div
                key={i}
                className="px-4 py-3 rounded-xl"
                style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
              >
                <div className="flex items-center gap-2 mb-1">{stat.icon}</div>
                <div
                  style={{
                    color: stat.accentColor,
                    fontWeight: 800,
                    fontSize: 22,
                    lineHeight: 1.1,
                  }}
                >
                  {stat.value}
                </div>
                <div style={{ color: "#848494", fontSize: 12, marginTop: 2 }}>{stat.label}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Charts section */}
      <div className="max-w-6xl mx-auto px-4 sm:px-6 mt-8">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Overlap bar chart */}
          <div
            className="p-6 rounded-2xl"
            style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
          >
            <div className="mb-4">
              <h3 style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 16, marginBottom: 4 }}>
                Top Audience Overlaps
              </h3>
              <p style={{ color: "#848494", fontSize: 13 }}>
                Channels sharing the most viewers with {channel.displayName}
              </p>
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={overlapData} layout="vertical" margin={{ left: 10, right: 20 }}>
                <XAxis
                  type="number"
                  tick={{ fill: "#848494", fontSize: 11, fontFamily: "'Space Grotesk', sans-serif" }}
                  axisLine={{ stroke: "#2A2A2E" }}
                  tickLine={false}
                  tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{ fill: "#EFEFF1", fontSize: 12, fontFamily: "'Space Grotesk', sans-serif" }}
                  axisLine={false}
                  tickLine={false}
                  width={100}
                />
                <Tooltip content={<CustomTooltipBar />} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
                <Bar dataKey="shared" fill="#00E5CC" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Viewer timeline */}
          <div
            className="p-6 rounded-2xl"
            style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
          >
            <div className="mb-4">
              <h3 style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 16, marginBottom: 4 }}>
                Viewer Count Trend
              </h3>
              <p style={{ color: "#848494", fontSize: 13 }}>
                Average concurrent viewers over the collection period
              </p>
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={channel.viewerHistory} margin={{ left: 5, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2A2A2E" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#848494", fontSize: 11, fontFamily: "'Space Grotesk', sans-serif" }}
                  axisLine={{ stroke: "#2A2A2E" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: "#848494", fontSize: 11, fontFamily: "'Space Grotesk', sans-serif" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
                />
                <Tooltip content={<CustomTooltipLine />} />
                <Line
                  type="monotone"
                  dataKey="viewers"
                  stroke={color}
                  strokeWidth={2}
                  dot={{ fill: color, r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5, fill: color }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Community membership */}
        <div
          className="mt-6 p-6 rounded-2xl"
          style={{
            background: "#18181B",
            border: `1px solid ${color}33`,
          }}
        >
          <h3 style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 16, marginBottom: 12 }}>
            Community Membership
          </h3>
          <div className="flex flex-col sm:flex-row items-start sm:items-center gap-6">
            <div className="flex items-center gap-3">
              <div
                className="rounded-xl flex items-center justify-center"
                style={{
                  width: 48,
                  height: 48,
                  background: color + "20",
                  border: `1px solid ${color}44`,
                }}
              >
                <div
                  className="rounded-full"
                  style={{ width: 16, height: 16, background: color, boxShadow: `0 0 10px ${color}` }}
                />
              </div>
              <div>
                <div style={{ color, fontWeight: 700, fontSize: 18 }}>
                  {community.label}
                </div>
                <div style={{ color: "#848494", fontSize: 13 }}>
                  {community.nodeCount} channels in this community
                </div>
              </div>
            </div>
            <div className="flex-1 grid grid-cols-2 sm:grid-cols-3 gap-4">
              <div>
                <div style={{ color: "#848494", fontSize: 11, marginBottom: 3, letterSpacing: "0.04em" }}>
                  MODULARITY SCORE
                </div>
                <div style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 20 }}>
                  {channel.modularityScore}
                </div>
                <div
                  className="mt-1.5 h-1.5 rounded-full overflow-hidden"
                  style={{ background: "#2A2A2E" }}
                >
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${channel.modularityScore * 100}%`,
                      background: color,
                    }}
                  />
                </div>
              </div>
              <div>
                <div style={{ color: "#848494", fontSize: 11, marginBottom: 3, letterSpacing: "0.04em" }}>
                  CONNECTIONS
                </div>
                <div style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 20 }}>
                  {channel.edgeCount}
                </div>
              </div>
              <div>
                <div style={{ color: "#848494", fontSize: 11, marginBottom: 3, letterSpacing: "0.04em" }}>
                  DESCRIPTION
                </div>
                <div style={{ color: "#848494", fontSize: 13, lineHeight: 1.5 }}>
                  {community.description}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Related channels */}
        {relatedChannels.length > 0 && (
          <div className="mt-8">
            <h3
              style={{
                color: "#EFEFF1",
                fontWeight: 700,
                fontSize: 18,
                marginBottom: 16,
                letterSpacing: "-0.01em",
              }}
            >
              More from {community.label}
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {relatedChannels.map((ch) => (
                <Link
                  key={ch.id}
                  to={`/channel/${ch.id}`}
                  style={{ textDecoration: "none" }}
                >
                  <div
                    className="p-4 rounded-xl transition-all"
                    style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.borderColor = color + "55";
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.borderColor = "#2A2A2E";
                    }}
                  >
                    <div className="flex items-center gap-3 mb-3">
                      <div
                        className="flex items-center justify-center rounded-xl flex-shrink-0"
                        style={{
                          width: 40,
                          height: 40,
                          background: color + "20",
                          color,
                          fontWeight: 800,
                          fontSize: 14,
                        }}
                      >
                        {getInitials(ch.displayName)}
                      </div>
                      <div>
                        <div style={{ color: "#EFEFF1", fontWeight: 600, fontSize: 14 }}>
                          {ch.displayName}
                        </div>
                        <div style={{ color: "#848494", fontSize: 12 }}>{ch.game}</div>
                      </div>
                    </div>
                    <div className="flex items-center justify-between">
                      <div style={{ color: color, fontWeight: 700, fontSize: 15 }}>
                        {ch.viewers.toLocaleString()}
                      </div>
                      <div style={{ color: "#848494", fontSize: 12 }}>viewers</div>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

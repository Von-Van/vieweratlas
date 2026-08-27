import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { Database, Users, Network, Activity, Clock, TrendingUp } from "lucide-react";
import { useAtlasData } from "../data/useAtlasData";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { Link } from "react-router";

const COMMUNITY_COLORS = [
  "#FF7B00", "#9147FF", "#1DB954", "#00E5CC", "#848494", "#FF4D6D", "#FFD700", "#4299E1",
];

const compactNumber = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

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
        <div style={{ color: "#9147FF", fontSize: 12 }}>
          {payload[0].value.toLocaleString()} channels
        </div>
        {payload[1] && (
          <div style={{ color: "#00E5CC", fontSize: 12 }}>
            {(payload[1].value / 1000).toFixed(0)}k viewers
          </div>
        )}
      </div>
    );
  }
  return null;
};

function MetricCard({
  icon,
  value,
  label,
  sub,
  accent,
}: {
  icon: React.ReactNode;
  value: string;
  label: string;
  sub?: string;
  accent: string;
}) {
  return (
    <div
      className="p-6 rounded-2xl"
      style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
    >
      <div
        className="w-10 h-10 rounded-xl flex items-center justify-center mb-4"
        style={{ background: accent + "18", border: `1px solid ${accent}33` }}
      >
        {icon}
      </div>
      <div
        style={{
          color: accent,
          fontWeight: 800,
          fontSize: 32,
          letterSpacing: "-0.03em",
          lineHeight: 1,
          marginBottom: 6,
          fontFamily: "'Space Grotesk', sans-serif",
        }}
      >
        {value}
      </div>
      <div style={{ color: "#EFEFF1", fontWeight: 600, fontSize: 14 }}>{label}</div>
      {sub && <div style={{ color: "#848494", fontSize: 12, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

export function Stats() {
  const { data, loading } = useAtlasData();

  if (loading || !data) return <LoadingSkeleton />;

  const { overallStats, topCommunitiesBySize, mostConnectedChannels } = data;

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
        className="py-12 px-6"
        style={{
          background: "linear-gradient(135deg, rgba(145,71,255,0.08) 0%, #0E0E10 60%)",
          borderBottom: "1px solid #2A2A2E",
        }}
      >
        <div className="max-w-6xl mx-auto">
          <div
            className="inline-block px-3 py-1 rounded-full text-xs mb-3"
            style={{
              background: "rgba(145, 71, 255, 0.12)",
              border: "1px solid rgba(145, 71, 255, 0.3)",
              color: "#9147FF",
              fontWeight: 600,
              letterSpacing: "0.05em",
            }}
          >
            OVERVIEW DASHBOARD
          </div>
          <h1
            style={{
              color: "#EFEFF1",
              fontSize: "clamp(1.75rem, 4vw, 2.5rem)",
              fontWeight: 800,
              letterSpacing: "-0.03em",
              marginBottom: 8,
            }}
          >
            Dataset Statistics
          </h1>
          <p style={{ color: "#848494", fontSize: 15, lineHeight: 1.6, maxWidth: 550 }}>
            Aggregated channel-level metrics produced by the ViewerAtlas analysis pipeline for {overallStats.collectionPeriod}.
          </p>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 sm:px-6 mt-8">
        {/* Key metrics */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5 mb-8">
          <MetricCard
            icon={<Database size={18} style={{ color: "#9147FF" }} />}
            value={overallStats.totalChannels.toLocaleString()}
            label="Total Channels Tracked"
            sub="Active in collection window"
            accent="#9147FF"
          />
          <MetricCard
            icon={<Users size={18} style={{ color: "#00E5CC" }} />}
            value={compactNumber.format(overallStats.totalViewers)}
            label="Unique Viewers Observed"
            sub="Distinct chat participants"
            accent="#00E5CC"
          />
          <MetricCard
            icon={<Network size={18} style={{ color: "#FF7B00" }} />}
            value={overallStats.communitiesDetected.toString()}
            label="Communities Detected"
            sub="Via Louvain algorithm"
            accent="#FF7B00"
          />
          <MetricCard
            icon={<Activity size={18} style={{ color: "#1DB954" }} />}
            value={overallStats.modularityScore.toString()}
            label="Modularity Score"
            sub="Graph quality (0–1 scale)"
            accent="#1DB954"
          />
          <MetricCard
            icon={<TrendingUp size={18} style={{ color: "#FF4D6D" }} />}
            value={overallStats.edgesTotal.toLocaleString()}
            label="Total Graph Edges"
            sub="Weighted viewer overlaps"
            accent="#FF4D6D"
          />
          <MetricCard
            icon={<Clock size={18} style={{ color: "#FFD700" }} />}
            value={compactNumber.format(overallStats.dataPoints)}
            label="Presence Data Points"
            sub={overallStats.collectionPeriod}
            accent="#FFD700"
          />
        </div>

        {/* Charts row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          {/* Top communities by size */}
          <div
            className="p-6 rounded-2xl"
            style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
          >
            <div className="mb-6">
              <h2 style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 17, marginBottom: 4 }}>
                Communities by Channel Count
              </h2>
              <p style={{ color: "#848494", fontSize: 13 }}>
                Top 8 detected communities ranked by member channels
              </p>
            </div>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart
                data={topCommunitiesBySize}
                layout="vertical"
                margin={{ left: 0, right: 16 }}
              >
                <XAxis
                  type="number"
                  tick={{ fill: "#848494", fontSize: 11, fontFamily: "'Space Grotesk', sans-serif" }}
                  axisLine={{ stroke: "#2A2A2E" }}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="community"
                  tick={{ fill: "#EFEFF1", fontSize: 12, fontFamily: "'Space Grotesk', sans-serif" }}
                  axisLine={false}
                  tickLine={false}
                  width={110}
                />
                <Tooltip content={<CustomTooltipBar />} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
                <Bar dataKey="channels" radius={[0, 4, 4, 0]}>
                  {topCommunitiesBySize.map((_, index) => (
                    <Cell key={index} fill={COMMUNITY_COLORS[index % COMMUNITY_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Top communities by viewers */}
          <div
            className="p-6 rounded-2xl"
            style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
          >
            <div className="mb-6">
              <h2 style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 17, marginBottom: 4 }}>
                Communities by Viewer Reach
              </h2>
              <p style={{ color: "#848494", fontSize: 13 }}>
                Estimated unique viewer reach per community
              </p>
            </div>
            <div className="flex flex-col gap-2.5">
              {topCommunitiesBySize.map((comm, i) => {
                const max = topCommunitiesBySize[0].viewers;
                const pct = (comm.viewers / max) * 100;
                const color = COMMUNITY_COLORS[i % COMMUNITY_COLORS.length];
                return (
                  // Rank position, not name: two communities can share a
                  // display label, and a duplicate key silently drops a row.
                  <div key={`${i}-${comm.community}`}>
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <div
                          className="rounded-full"
                          style={{ width: 8, height: 8, background: color, flexShrink: 0 }}
                        />
                        <span style={{ color: "#EFEFF1", fontSize: 13 }}>{comm.community}</span>
                      </div>
                      <span style={{ color: color, fontWeight: 700, fontSize: 13 }}>
                        {(comm.viewers / 1000000).toFixed(1)}M
                      </span>
                    </div>
                    <div
                      className="rounded-full overflow-hidden"
                      style={{ height: 6, background: "#2A2A2E" }}
                    >
                      <div
                        className="h-full rounded-full transition-all"
                        style={{ width: `${pct}%`, background: color }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Most connected channels */}
        <div
          className="p-6 rounded-2xl mb-8"
          style={{ background: "#18181B", border: "1px solid #2A2A2E" }}
        >
          <div className="flex items-start justify-between mb-6">
            <div>
              <h2 style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 17, marginBottom: 4 }}>
                Most Connected Channels
              </h2>
              <p style={{ color: "#848494", fontSize: 13 }}>
                Channels with the highest number of weighted edges in the overlap graph
              </p>
            </div>
            <Link
              to="/map"
              className="px-3 py-1.5 rounded-lg text-xs transition-all"
              style={{
                border: "1px solid #2A2A2E",
                color: "#848494",
                textDecoration: "none",
                fontWeight: 500,
                flexShrink: 0,
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.color = "#9147FF";
                (e.currentTarget as HTMLElement).style.borderColor = "#9147FF";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.color = "#848494";
                (e.currentTarget as HTMLElement).style.borderColor = "#2A2A2E";
              }}
            >
              View on Map
            </Link>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {mostConnectedChannels.map((ch, i) => (
              <div
                key={`${i}-${ch.name}`}
                className="flex items-center gap-4 px-4 py-3 rounded-xl"
                style={{ background: "#0E0E10", border: "1px solid #2A2A2E" }}
              >
                <div
                  style={{
                    color: "#848494",
                    fontSize: 13,
                    fontWeight: 700,
                    width: 20,
                    flexShrink: 0,
                  }}
                >
                  #{i + 1}
                </div>
                <div
                  className="flex items-center justify-center rounded-xl flex-shrink-0"
                  style={{
                    width: 36,
                    height: 36,
                    background: ch.color + "20",
                    color: ch.color,
                    fontWeight: 800,
                    fontSize: 13,
                  }}
                >
                  {ch.name.slice(0, 2).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div
                    style={{
                      color: "#EFEFF1",
                      fontWeight: 600,
                      fontSize: 14,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {ch.name}
                  </div>
                  <div
                    className="inline-flex items-center gap-1 mt-0.5"
                    style={{ color: ch.color, fontSize: 11, fontWeight: 600 }}
                  >
                    <div
                      className="rounded-full"
                      style={{ width: 5, height: 5, background: ch.color }}
                    />
                    {ch.community}
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  <div style={{ color: ch.color, fontWeight: 800, fontSize: 18 }}>
                    {ch.edges}
                  </div>
                  <div style={{ color: "#848494", fontSize: 11 }}>edges</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Data methodology note */}
        <div
          className="p-6 rounded-2xl"
          style={{
            background: "rgba(145, 71, 255, 0.05)",
            border: "1px solid rgba(145, 71, 255, 0.2)",
          }}
        >
          <h3 style={{ color: "#9147FF", fontWeight: 700, fontSize: 15, marginBottom: 8 }}>
            Data Collection Methodology
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              {
                label: "Sampling Rate",
                value: "Every 60 min",
                desc: "Chat presence logged hourly",
              },
              {
                label: "Data Points",
                value: "2.84M",
                desc: "Total viewer-channel observations",
              },
              {
                label: "Avg Overlap Weight",
                value: "3,241",
                desc: "Mean shared chatters per edge",
              },
            ].map((m) => (
              <div key={m.label}>
                <div style={{ color: "#848494", fontSize: 11, letterSpacing: "0.04em" }}>
                  {m.label.toUpperCase()}
                </div>
                <div style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 18, margin: "4px 0" }}>
                  {m.value}
                </div>
                <div style={{ color: "#848494", fontSize: 13 }}>{m.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

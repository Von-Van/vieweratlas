import { useState, useMemo } from "react";
import { useNavigate } from "react-router";
import { X, Search, SlidersHorizontal, Info, ExternalLink } from "lucide-react";
import { NetworkGraph } from "../components/NetworkGraph";
import { useAtlasData } from "../data/useAtlasData";
import { LoadingSkeleton } from "../components/LoadingSkeleton";

const LANG_OPTIONS = ["All", "English", "Japanese", "Spanish"];
const GAME_OPTIONS = ["All", "FPS", "MOBA", "Variety", "IRL", "Strategy", "Speedrun"];

export function CommunityMap() {
  const navigate = useNavigate();
  const { data, loading } = useAtlasData();
  const [selectedCommunities, setSelectedCommunities] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [minViewers, setMinViewers] = useState(0);
  const [selectedLanguage, setSelectedLanguage] = useState("All");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [filtersExpanded, setFiltersExpanded] = useState(false);

  const channels = data?.channels ?? [];
  const edges = data?.edges ?? [];
  const communities = data?.communities ?? [];

  const toggleCommunity = (id: string) => {
    setSelectedCommunities((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filteredChannels = useMemo(() => {
    return channels.filter((ch) => {
      if (selectedCommunities.size > 0 && !selectedCommunities.has(ch.communityId)) return false;
      if (ch.viewers < minViewers) return false;
      if (selectedLanguage !== "All" && ch.language !== selectedLanguage) return false;
      if (searchQuery && !ch.displayName.toLowerCase().includes(searchQuery.toLowerCase()) && !ch.game.toLowerCase().includes(searchQuery.toLowerCase())) return false;
      return true;
    });
  }, [selectedCommunities, minViewers, selectedLanguage, searchQuery]);

  const filteredEdges = useMemo(() => {
    const ids = new Set(filteredChannels.map((c) => c.id));
    return edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  }, [filteredChannels]);

  const selectedChannel = selectedNode ? channels.find((c) => c.id === selectedNode) : null;
  const selectedChannelCommunity = selectedChannel
    ? communities.find((c) => c.id === selectedChannel.communityId)
    : null;

  const communityChannelCounts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const ch of filteredChannels) {
      map[ch.communityId] = (map[ch.communityId] || 0) + 1;
    }
    return map;
  }, [filteredChannels]);

  if (loading || !data) return <LoadingSkeleton />;

  return (
    <div
      className="flex"
      style={{
        height: "calc(100vh - 64px)",
        background: "#0E0E10",
        fontFamily: "'Space Grotesk', sans-serif",
      }}
    >
      {/* Sidebar */}
      {sidebarOpen && (
        <div
          className="flex-shrink-0 flex flex-col overflow-hidden"
          style={{
            width: 300,
            background: "#18181B",
            borderRight: "1px solid #2A2A2E",
            overflowY: "auto",
          }}
        >
          {/* Header */}
          <div
            className="px-4 py-4 flex items-center justify-between flex-shrink-0"
            style={{ borderBottom: "1px solid #2A2A2E" }}
          >
            <div>
              <div style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 15 }}>Community Map</div>
              <div style={{ color: "#848494", fontSize: 12, marginTop: 2 }}>
                {filteredChannels.length} channels · {filteredEdges.length} edges
              </div>
            </div>
            <button
              onClick={() => setSidebarOpen(false)}
              style={{
                background: "transparent",
                border: "none",
                color: "#848494",
                cursor: "pointer",
                padding: 4,
                borderRadius: 6,
              }}
            >
              <X size={16} />
            </button>
          </div>

          {/* Search */}
          <div className="px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid #2A2A2E" }}>
            <div className="relative">
              <Search
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2"
                style={{ color: "#848494" }}
              />
              <input
                type="text"
                placeholder="Search channels..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-3 py-2 rounded-lg text-sm"
                style={{
                  background: "#0E0E10",
                  border: "1px solid #2A2A2E",
                  color: "#EFEFF1",
                  outline: "none",
                  fontFamily: "'Space Grotesk', sans-serif",
                }}
              />
            </div>
          </div>

          {/* Filters */}
          <div className="px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid #2A2A2E" }}>
            <button
              className="flex items-center gap-2 w-full text-left"
              onClick={() => setFiltersExpanded(!filtersExpanded)}
              style={{
                background: "transparent",
                border: "none",
                color: "#848494",
                cursor: "pointer",
                fontFamily: "'Space Grotesk', sans-serif",
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "0.05em",
                padding: 0,
              }}
            >
              <SlidersHorizontal size={13} />
              FILTERS
              <span style={{ marginLeft: "auto", fontSize: 11 }}>{filtersExpanded ? "▲" : "▼"}</span>
            </button>

            {filtersExpanded && (
              <div className="mt-3 flex flex-col gap-3">
                {/* Language */}
                <div>
                  <div style={{ color: "#848494", fontSize: 11, marginBottom: 6, letterSpacing: "0.04em" }}>
                    LANGUAGE
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {LANG_OPTIONS.map((l) => (
                      <button
                        key={l}
                        onClick={() => setSelectedLanguage(l)}
                        style={{
                          background: selectedLanguage === l ? "rgba(145,71,255,0.2)" : "#0E0E10",
                          border: `1px solid ${selectedLanguage === l ? "#9147FF" : "#2A2A2E"}`,
                          color: selectedLanguage === l ? "#9147FF" : "#848494",
                          borderRadius: 6,
                          padding: "3px 10px",
                          fontSize: 12,
                          cursor: "pointer",
                          fontFamily: "'Space Grotesk', sans-serif",
                          fontWeight: 500,
                        }}
                      >
                        {l}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Min viewers */}
                <div>
                  <div
                    className="flex items-center justify-between"
                    style={{ marginBottom: 6 }}
                  >
                    <div style={{ color: "#848494", fontSize: 11, letterSpacing: "0.04em" }}>
                      MIN VIEWERS
                    </div>
                    <div style={{ color: "#9147FF", fontSize: 12, fontWeight: 600 }}>
                      {minViewers.toLocaleString()}
                    </div>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={20000}
                    step={500}
                    value={minViewers}
                    onChange={(e) => setMinViewers(Number(e.target.value))}
                    className="w-full"
                    style={{ accentColor: "#9147FF" }}
                  />
                </div>

                {minViewers > 0 || selectedLanguage !== "All" ? (
                  <button
                    onClick={() => { setMinViewers(0); setSelectedLanguage("All"); }}
                    style={{
                      background: "transparent",
                      border: "1px solid #2A2A2E",
                      color: "#848494",
                      borderRadius: 6,
                      padding: "4px 10px",
                      fontSize: 12,
                      cursor: "pointer",
                      fontFamily: "'Space Grotesk', sans-serif",
                      alignSelf: "flex-start",
                    }}
                  >
                    Reset filters
                  </button>
                ) : null}
              </div>
            )}
          </div>

          {/* Community Legend */}
          <div className="px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid #2A2A2E" }}>
            <div
              style={{
                color: "#848494",
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.05em",
                marginBottom: 10,
              }}
            >
              COMMUNITIES
            </div>
            <div className="flex flex-col gap-1.5">
              {communities.map((comm) => {
                const count = communityChannelCounts[comm.id] || 0;
                const active = selectedCommunities.size === 0 || selectedCommunities.has(comm.id);
                return (
                  <button
                    key={comm.id}
                    onClick={() => toggleCommunity(comm.id)}
                    className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg w-full text-left transition-all"
                    style={{
                      background: selectedCommunities.has(comm.id)
                        ? `${comm.color}18`
                        : "transparent",
                      border: `1px solid ${selectedCommunities.has(comm.id) ? comm.color + "44" : "transparent"}`,
                      opacity: active ? 1 : 0.45,
                      cursor: "pointer",
                    }}
                  >
                    <div
                      className="rounded-full flex-shrink-0"
                      style={{
                        width: 10,
                        height: 10,
                        background: comm.color,
                        boxShadow: `0 0 6px ${comm.color}88`,
                      }}
                    />
                    <span style={{ color: "#EFEFF1", fontSize: 13, flex: 1 }}>
                      {comm.label}
                    </span>
                    <span
                      className="rounded-full px-1.5 py-0.5"
                      style={{
                        background: "#0E0E10",
                        color: "#848494",
                        fontSize: 11,
                        fontWeight: 600,
                      }}
                    >
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>
            {selectedCommunities.size > 0 && (
              <button
                onClick={() => setSelectedCommunities(new Set())}
                style={{
                  marginTop: 8,
                  background: "transparent",
                  border: "none",
                  color: "#848494",
                  fontSize: 12,
                  cursor: "pointer",
                  fontFamily: "'Space Grotesk', sans-serif",
                  padding: "2px 0",
                  textDecoration: "underline",
                }}
              >
                Show all communities
              </button>
            )}
          </div>

          {/* Selected node detail */}
          {selectedChannel && selectedChannelCommunity && (
            <div className="px-4 py-4 flex-shrink-0" style={{ borderBottom: "1px solid #2A2A2E" }}>
              <div
                style={{
                  color: "#848494",
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.05em",
                  marginBottom: 10,
                }}
              >
                SELECTED CHANNEL
              </div>
              <div
                className="p-3 rounded-xl"
                style={{
                  background: "#0E0E10",
                  border: `1px solid ${selectedChannelCommunity.color}44`,
                }}
              >
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <div style={{ color: selectedChannelCommunity.color, fontWeight: 700, fontSize: 15 }}>
                      {selectedChannel.displayName}
                    </div>
                    <div style={{ color: "#848494", fontSize: 12 }}>{selectedChannel.game}</div>
                  </div>
                  <button
                    onClick={() => setSelectedNode(null)}
                    style={{ background: "transparent", border: "none", color: "#848494", cursor: "pointer", padding: 2 }}
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="flex items-center gap-3 mb-3">
                  <div>
                    <div style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 16 }}>
                      {selectedChannel.viewers.toLocaleString()}
                    </div>
                    <div style={{ color: "#848494", fontSize: 11 }}>viewers</div>
                  </div>
                  <div>
                    <div style={{ color: "#EFEFF1", fontWeight: 700, fontSize: 16 }}>
                      {selectedChannel.edgeCount}
                    </div>
                    <div style={{ color: "#848494", fontSize: 11 }}>connections</div>
                  </div>
                </div>
                <div
                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs mb-3"
                  style={{
                    background: selectedChannelCommunity.color + "18",
                    color: selectedChannelCommunity.color,
                    fontWeight: 600,
                  }}
                >
                  <div
                    className="rounded-full"
                    style={{ width: 6, height: 6, background: selectedChannelCommunity.color }}
                  />
                  {selectedChannelCommunity.label}
                </div>

                <div style={{ marginBottom: 8 }}>
                  <div style={{ color: "#848494", fontSize: 11, marginBottom: 6 }}>TOP OVERLAPS</div>
                  {selectedChannel.topOverlaps.slice(0, 3).map((ov) => (
                    <div
                      key={ov.channelId}
                      className="flex items-center justify-between py-1"
                    >
                      <span style={{ color: "#EFEFF1", fontSize: 13 }}>{ov.channelName}</span>
                      <span
                        className="px-1.5 py-0.5 rounded text-xs"
                        style={{ background: "#18181B", color: "#00E5CC", fontWeight: 600 }}
                      >
                        {(ov.shared / 1000).toFixed(1)}k
                      </span>
                    </div>
                  ))}
                </div>

                <button
                  onClick={() => navigate(`/channel/${selectedChannel.id}`)}
                  className="w-full flex items-center justify-center gap-2 py-2 rounded-lg text-sm transition-all"
                  style={{
                    background: selectedChannelCommunity.color,
                    color: "#fff",
                    border: "none",
                    cursor: "pointer",
                    fontFamily: "'Space Grotesk', sans-serif",
                    fontWeight: 600,
                  }}
                >
                  <ExternalLink size={13} />
                  View Full Profile
                </button>
              </div>
            </div>
          )}

          {/* Instructions */}
          <div className="px-4 py-4 mt-auto">
            <div
              className="flex items-start gap-2 p-3 rounded-lg"
              style={{ background: "#0E0E10", border: "1px solid #2A2A2E" }}
            >
              <Info size={14} style={{ color: "#848494", marginTop: 1, flexShrink: 0 }} />
              <p style={{ color: "#848494", fontSize: 12, lineHeight: 1.6 }}>
                <strong style={{ color: "#EFEFF1" }}>Click</strong> a node to inspect.{" "}
                <strong style={{ color: "#EFEFF1" }}>Drag</strong> to pan.{" "}
                <strong style={{ color: "#EFEFF1" }}>Scroll</strong> to zoom.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Graph area */}
      <div className="flex-1 relative">
        {/* Toggle sidebar btn */}
        {!sidebarOpen && (
          <button
            onClick={() => setSidebarOpen(true)}
            className="absolute top-4 left-4 z-10 flex items-center gap-2 px-3 py-2 rounded-lg text-sm"
            style={{
              background: "#18181B",
              border: "1px solid #2A2A2E",
              color: "#848494",
              cursor: "pointer",
              fontFamily: "'Space Grotesk', sans-serif",
              fontWeight: 500,
            }}
          >
            <SlidersHorizontal size={14} />
            Show Panel
          </button>
        )}

        {/* Controls overlay */}
        <div
          className="absolute top-4 right-4 z-10 flex flex-col gap-2 items-end"
          style={{ pointerEvents: "none" }}
        >
          <div
            className="px-3 py-1.5 rounded-lg text-xs"
            style={{
              background: "rgba(24,24,27,0.85)",
              border: "1px solid #2A2A2E",
              color: "#848494",
              backdropFilter: "blur(8px)",
              pointerEvents: "auto",
            }}
          >
            {filteredChannels.length} nodes · {filteredEdges.length} edges
          </div>
        </div>

        <NetworkGraph
          channels={filteredChannels}
          edges={filteredEdges}
          communities={communities}
          className="w-full h-full"
          interactive={true}
          onNodeClick={(id) => setSelectedNode(id)}
          highlightedNode={selectedNode}
        />
      </div>
    </div>
  );
}

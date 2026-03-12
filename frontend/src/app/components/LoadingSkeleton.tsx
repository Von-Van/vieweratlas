export function LoadingSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div
      className="flex flex-col items-center justify-center py-40"
      style={{
        color: "#848494",
        fontFamily: "'Space Grotesk', sans-serif",
      }}
    >
      <div
        className="mb-6"
        style={{
          width: 40,
          height: 40,
          borderRadius: "50%",
          border: "3px solid #2A2A2E",
          borderTopColor: "#9147FF",
          animation: "spin 0.8s linear infinite",
        }}
      />
      <div style={{ color: "#EFEFF1", fontWeight: 600, fontSize: 16, marginBottom: 8 }}>
        Loading data...
      </div>
      <div className="flex flex-col gap-3 w-64">
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className="rounded"
            style={{
              height: 12,
              background: "#2A2A2E",
              width: `${80 - i * 15}%`,
              animation: "pulse 1.5s ease-in-out infinite",
              animationDelay: `${i * 0.15}s`,
            }}
          />
        ))}
      </div>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  );
}

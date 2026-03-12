import { Link, useLocation, Outlet } from "react-router";
import { useState } from "react";
import { Menu, X, Globe, Github } from "lucide-react";

const navLinks = [
  { to: "/map", label: "Community Map" },
  { to: "/stats", label: "Stats" },
  { to: "/about", label: "How It Works" },
];

export function Layout() {
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div style={{ background: "#0E0E10", minHeight: "100vh", color: "#EFEFF1" }}>
      {/* Navbar */}
      <nav
        className="sticky top-0 z-50"
        style={{
          background: "rgba(14, 14, 16, 0.85)",
          backdropFilter: "blur(12px)",
          borderBottom: "1px solid #2A2A2E",
        }}
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between h-16">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2 group" style={{ textDecoration: "none" }}>
            <div
              className="flex items-center justify-center rounded-lg"
              style={{
                width: 32,
                height: 32,
                background: "linear-gradient(135deg, #9147FF, #00E5CC)",
              }}
            >
              <Globe size={18} style={{ color: "#fff" }} />
            </div>
            <span
              style={{
                color: "#EFEFF1",
                fontFamily: "'Space Grotesk', sans-serif",
                fontWeight: 700,
                fontSize: 18,
                letterSpacing: "-0.02em",
              }}
            >
              Viewer<span style={{ color: "#9147FF" }}>Atlas</span>
            </span>
          </Link>

          {/* Desktop nav */}
          <div className="hidden md:flex items-center gap-1">
            {navLinks.map((link) => {
              const active = location.pathname === link.to;
              return (
                <Link
                  key={link.to}
                  to={link.to}
                  style={{
                    color: active ? "#9147FF" : "#848494",
                    textDecoration: "none",
                    fontFamily: "'Space Grotesk', sans-serif",
                    fontWeight: 500,
                    fontSize: 14,
                    padding: "6px 14px",
                    borderRadius: 8,
                    background: active ? "rgba(145, 71, 255, 0.12)" : "transparent",
                    transition: "all 0.15s",
                  }}
                  onMouseEnter={(e) => {
                    if (!active) (e.target as HTMLElement).style.color = "#EFEFF1";
                  }}
                  onMouseLeave={(e) => {
                    if (!active) (e.target as HTMLElement).style.color = "#848494";
                  }}
                >
                  {link.label}
                </Link>
              );
            })}
          </div>

          {/* Right side */}
          <div className="hidden md:flex items-center gap-3">
            <a
              href="https://github.com/Von-Van/vieweratlas"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all"
              style={{
                color: "#848494",
                border: "1px solid #2A2A2E",
                textDecoration: "none",
                fontFamily: "'Space Grotesk', sans-serif",
                fontSize: 13,
                fontWeight: 500,
                background: "transparent",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.color = "#EFEFF1";
                (e.currentTarget as HTMLElement).style.borderColor = "#9147FF";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.color = "#848494";
                (e.currentTarget as HTMLElement).style.borderColor = "#2A2A2E";
              }}
            >
              <Github size={15} />
              GitHub
            </a>
            <Link
              to="/map"
              style={{
                background: "#9147FF",
                color: "#fff",
                textDecoration: "none",
                fontFamily: "'Space Grotesk', sans-serif",
                fontSize: 13,
                fontWeight: 600,
                padding: "7px 16px",
                borderRadius: 8,
                transition: "all 0.15s",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.background = "#7c2fd4";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.background = "#9147FF";
              }}
            >
              Explore Map
            </Link>
          </div>

          {/* Mobile menu btn */}
          <button
            className="md:hidden p-2 rounded-lg"
            style={{ color: "#848494", background: "transparent", border: "none", cursor: "pointer" }}
            onClick={() => setMobileOpen(!mobileOpen)}
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>

        {/* Mobile menu */}
        {mobileOpen && (
          <div
            className="md:hidden px-4 pb-4 flex flex-col gap-1"
            style={{ borderTop: "1px solid #2A2A2E" }}
          >
            {navLinks.map((link) => (
              <Link
                key={link.to}
                to={link.to}
                onClick={() => setMobileOpen(false)}
                style={{
                  color: location.pathname === link.to ? "#9147FF" : "#848494",
                  textDecoration: "none",
                  padding: "10px 12px",
                  borderRadius: 8,
                  fontFamily: "'Space Grotesk', sans-serif",
                  fontWeight: 500,
                  fontSize: 15,
                  background: location.pathname === link.to ? "rgba(145, 71, 255, 0.12)" : "transparent",
                }}
              >
                {link.label}
              </Link>
            ))}
            <div className="mt-2 flex flex-col gap-2">
              <a
                href="https://github.com/Von-Van/vieweratlas"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 px-3 py-2 rounded-lg"
                style={{
                  color: "#848494",
                  border: "1px solid #2A2A2E",
                  textDecoration: "none",
                  fontSize: 14,
                  fontWeight: 500,
                }}
              >
                <Github size={15} />
                View on GitHub
              </a>
            </div>
          </div>
        )}
      </nav>

      {/* Main */}
      <main><Outlet /></main>

      {/* Footer */}
      <footer
        className="mt-20 py-12 px-6"
        style={{ borderTop: "1px solid #2A2A2E" }}
      >
        <div className="max-w-7xl mx-auto">
          <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-8">
            <div>
              <div className="flex items-center gap-2 mb-3">
                <div
                  className="flex items-center justify-center rounded-lg"
                  style={{
                    width: 28,
                    height: 28,
                    background: "linear-gradient(135deg, #9147FF, #00E5CC)",
                  }}
                >
                  <Globe size={14} style={{ color: "#fff" }} />
                </div>
                <span
                  style={{
                    color: "#EFEFF1",
                    fontFamily: "'Space Grotesk', sans-serif",
                    fontWeight: 700,
                    fontSize: 16,
                  }}
                >
                  Viewer<span style={{ color: "#9147FF" }}>Atlas</span>
                </span>
              </div>
              <p style={{ color: "#848494", fontSize: 13, maxWidth: 300, lineHeight: 1.6 }}>
                Mapping the Twitch universe through shared viewer communities. Open-source data analytics tool.
              </p>
            </div>

            <div className="flex flex-wrap gap-8">
              <div>
                <div style={{ color: "#EFEFF1", fontWeight: 600, fontSize: 13, marginBottom: 12 }}>
                  Pages
                </div>
                {[
                  { to: "/", label: "Home" },
                  { to: "/map", label: "Community Map" },
                  { to: "/stats", label: "Stats" },
                  { to: "/about", label: "How It Works" },
                ].map((l) => (
                  <div key={l.to} style={{ marginBottom: 8 }}>
                    <Link
                      to={l.to}
                      style={{ color: "#848494", textDecoration: "none", fontSize: 13 }}
                    >
                      {l.label}
                    </Link>
                  </div>
                ))}
              </div>
              <div>
                <div style={{ color: "#EFEFF1", fontWeight: 600, fontSize: 13, marginBottom: 12 }}>
                  Project
                </div>
                {[
                  { href: "https://github.com/Von-Van/vieweratlas", label: "GitHub Repo" },
                  { href: "#", label: "Data Pipeline" },
                  { href: "#", label: "Privacy Policy" },
                ].map((l) => (
                  <div key={l.label} style={{ marginBottom: 8 }}>
                    <a
                      href={l.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "#848494", textDecoration: "none", fontSize: 13 }}
                    >
                      {l.label}
                    </a>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div
            className="mt-8 pt-6 flex flex-col md:flex-row items-center justify-between gap-3"
            style={{ borderTop: "1px solid #2A2A2E" }}
          >
            <p style={{ color: "#848494", fontSize: 12 }}>
              © 2026 ViewerAtlas. Open-source under MIT License.
            </p>
            <p style={{ color: "#848494", fontSize: 12 }}>
              Not affiliated with Twitch Interactive, Inc.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
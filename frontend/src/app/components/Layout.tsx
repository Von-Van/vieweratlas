import { Link, Outlet, ScrollRestoration, useLocation } from "react-router";
import { useAtlasData } from "../data/useAtlasData";
import { AUTHOR_URL, REPO_URL, TWITCH_ATLAS_URL } from "../lib/links";

const NAV = [
  { to: "/", label: "overview" },
  { to: "/map", label: "map" },
  { to: "/results", label: "results" },
  { to: "/methods", label: "methods" },
];

export function Layout() {
  const { pathname } = useLocation();
  const { source, notice } = useAtlasData();

  return (
    <div className="page">
      <header className="site-head">
        <div className="site-head__top">
          <Link to="/" className="site-title">
            ViewerAtlas
          </Link>
          <nav className="site-nav" aria-label="Site">
            {NAV.map((item) =>
              pathname === item.to ? (
                <span key={item.to} aria-current="page">
                  {item.label}
                </span>
              ) : (
                <Link key={item.to} to={item.to}>
                  {item.label}
                </Link>
              ),
            )}
            <a href={REPO_URL}>code</a>
          </nav>
        </div>
        <p className="site-sub">
          Which Twitch channels share an audience? Made by <a href={AUTHOR_URL}>Von-Van</a>.
          Inspired by Kiran Gershenfeld's <a href={TWITCH_ATLAS_URL}>Twitch Atlas</a> (2020).
        </p>
      </header>
      <hr className="rule" />

      {notice && (
        <p className="notice" role="status">
          <span className="tag">{source === "demo" ? "DEMO DATA" : "NOTE"}</span> {notice}
        </p>
      )}

      <main>
        <Outlet />
      </main>

      {/* Initial entries share "default", so distinguish their full URLs.
          Include the fragment: a saved position for /methods must not override
          a new /methods#threshold link. Later entries restore by history key. */}
      <ScrollRestoration
        getKey={(location) =>
          location.key === "default"
            ? location.pathname + location.search + location.hash
            : location.key
        }
      />
    </div>
  );
}

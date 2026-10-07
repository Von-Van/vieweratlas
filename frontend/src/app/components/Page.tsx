import { useEffect, type CSSProperties, type ReactNode } from "react";
import { SNAPSHOT } from "../lib/snapshot";

const SITE_TITLE = "ViewerAtlas: which Twitch channels share an audience?";

export function usePageTitle(title: string | null) {
  useEffect(() => {
    document.title = title ? `${title} · ViewerAtlas` : SITE_TITLE;
  }, [title]);
}

/**
 * One band of the reading column: the text on the left, its margin note on
 * the right. On narrow screens the note drops under the text.
 */
export function Row({
  children,
  aside,
  top,
  asideTop = 0,
  gap,
}: {
  children: ReactNode;
  aside?: ReactNode;
  /** Space above this band, in px. */
  top?: number;
  /** Nudges the note down to sit beside the paragraph it annotates. */
  asideTop?: number;
  gap?: number;
}) {
  return (
    <div className="row" style={top ? { marginTop: top } : undefined}>
      <div className="col" style={gap ? { gap } : undefined}>
        {children}
      </div>
      {aside ? (
        <aside className="aside" style={{ "--aside-top": `${asideTop}px` } as CSSProperties}>
          {aside}
        </aside>
      ) : null}
    </div>
  );
}

export function PageFooter({ children }: { children: ReactNode }) {
  return <footer className="site-foot">{children}</footer>;
}

/** Marks a passage quoted from the fixed 14-day run rather than the window on show. */
export function SnapshotNote() {
  return (
    <p className="snapshot">
      <span className="tag">SNAPSHOT</span> {SNAPSHOT.label}. Doesn't change with the window.
    </p>
  );
}

/**
 * Shown where a figure would be when the selected window has no data yet.
 * `days` is null when no window at all is ready.
 */
export function NotReady({
  days,
  fallback,
  onFallback,
  inline = false,
}: {
  days: number | null;
  fallback?: number;
  onFallback?: () => void;
  inline?: boolean;
}) {
  return (
    <div className={inline ? "not-ready not-ready--inline" : "not-ready"} role="status" aria-live="polite">
      <p className="tag" style={{ fontSize: 13, padding: "1px 6px" }}>NOT ENOUGH DATA YET</p>
      <p className="lede">
        {days
          ? `A ${days}-day window needs ${days} days of surveys. It will show up here on its own once they exist. Nothing needs redeploying.`
          : "Collection hasn't run long enough to fill any window yet. The map will show up here on its own once it has. Nothing needs redeploying."}
      </p>
      {fallback && onFallback ? (
        <p style={{ fontSize: 16 }}>
          <button type="button" className="linklike" onClick={onFallback}>
            Show the {fallback}-day window instead
          </button>
        </p>
      ) : null}
    </div>
  );
}

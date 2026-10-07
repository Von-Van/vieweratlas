import { Row, usePageTitle } from "./Page";

export function RouteError() {
  usePageTitle("Page couldn't load");
  return (
    <div className="page">
      <a className="site-title" href="/">ViewerAtlas</a>
      <hr className="rule" />
      <main className="body">
        <Row gap={14}>
          <h1 className="title">Page couldn't load</h1>
          <p>Refresh the page to try again, or open the overview.</p>
          <p style={{ display: "flex", gap: 20 }}>
            <button type="button" className="linklike" onClick={() => window.location.reload()}>
              Refresh page
            </button>
            <a href="/">Overview</a>
          </p>
        </Row>
      </main>
    </div>
  );
}

import { Link, useLocation } from "react-router";
import { Row, usePageTitle } from "../components/Page";

export function NotFound() {
  usePageTitle("Page not found");
  const { pathname } = useLocation();
  return (
    <div className="body">
      <Row gap={14}>
        <h1 className="title">Page not found</h1>
        <p className="pretty">
          Nothing lives at <code>{pathname}</code>. Try the <Link to="/">overview</Link> or the{" "}
          <Link to="/map">map</Link>.
        </p>
      </Row>
    </div>
  );
}

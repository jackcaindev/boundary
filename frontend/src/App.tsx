import { useEffect, useState } from "react";
import { AppLink } from "./components/AppLink";
import { CampaignPage } from "./pages/CampaignPage";
import { ComparisonPage } from "./pages/ComparisonPage";
import { RegressionPage } from "./pages/RegressionPage";
import { RunPage } from "./pages/RunPage";
import { resolveRoute } from "./routes/router";

export function App() {
  const [locationKey, setLocationKey] = useState(() => `${window.location.pathname}${window.location.search}`);
  useEffect(() => {
    const update = (): void => setLocationKey(`${window.location.pathname}${window.location.search}`);
    window.addEventListener("popstate", update);
    window.addEventListener("boundary:navigate", update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener("boundary:navigate", update);
    };
  }, []);
  const route = resolveRoute(window.location.pathname);
  return (
    <div className="app-shell" key={locationKey}>
      <div className="topbar">
        <AppLink href="/" className="wordmark" aria-label="Boundary home">Boundary<span className="wordmark-mark">/</span></AppLink>
        <span className="topbar-context">Phase 1 · tool-timeout campaign</span>
      </div>
      {route.kind === "campaign" ? <CampaignPage /> : null}
      {route.kind === "run" ? <RunPage runId={route.runId} /> : null}
      {route.kind === "regression" ? <RegressionPage regressionCaseId={route.regressionCaseId} /> : null}
      {route.kind === "comparison" ? <ComparisonPage comparisonId={route.comparisonId} /> : null}
      {route.kind === "not-found" ? (
        <main><div className="state-message"><h1>Route not found</h1><p>Boundary exposes exactly four application routes.</p><AppLink href="/">Return to campaign start</AppLink></div></main>
      ) : null}
      <footer><span>Boundary</span><span>Evidence, not instructions.</span></footer>
    </div>
  );
}

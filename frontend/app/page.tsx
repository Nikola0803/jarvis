import MiraHud from "@/components/MiraHud";
import MiraOverviewPanel from "@/components/MiraOverviewPanel";

export default function Home() {
  return (
    <main id="main" style={{ position: "relative", height: "100vh", minHeight: 620, overflow: "hidden" }}>
      {/* Top-left overview HUD: clock, weather, on this day, social links */}
      <MiraOverviewPanel />

      {/* The world: field, ring, particle core, centre lines, status bar. Tap the orb. */}
      <MiraHud />

      {/* Z.E.R.O version tag */}
      <div className="repo-pill" style={{ pointerEvents: "none", cursor: "default" }}>
        Z.E.R.O v1
      </div>
    </main>
  );
}

import type { Metadata } from "next";
import { AutopilotWorkspace } from "./workspace";

export const metadata: Metadata = { title: "Autopilot | Cliptric" };

export default function AutopilotPage() {
  return (
    <section className="project-workspace">
      <header className="workspace-heading">
        <div>
          <h1>Autopilot</h1>
          <p>Run Twitch scans and review the clips they prepare.</p>
        </div>
      </header>
      <AutopilotWorkspace />
    </section>
  );
}

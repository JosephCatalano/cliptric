import { TwitchDiscovery } from "./twitch-discovery";

export default function DiscoverPage() {
  return (
    <section className="project-workspace">
      <header className="workspace-heading">
        <div>
          <h1>Discover</h1>
          <p>Find Twitch clips by streamer or game.</p>
        </div>
      </header>
      <TwitchDiscovery />
    </section>
  );
}

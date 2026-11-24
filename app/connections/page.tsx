import { platforms } from "@/lib/social";
import { SocialConnection } from "../components/social-connection";
import { PublishingNavigation } from "../components/publishing-navigation";

export default function ConnectionsPage() {
  return (
    <section className="project-workspace">
      <header className="workspace-heading">
        <div><h1>Accounts</h1><p>Manage the destinations you publish to from Cliptric.</p></div>
      </header>
      <PublishingNavigation current="accounts" />
      <div className="grid items-start gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {platforms.map((platform) => <SocialConnection key={platform} platform={platform} />)}
      </div>
      <p className="text-sm text-muted-foreground">Disconnecting an account keeps its publishing history and previously published posts.</p>
    </section>
  );
}

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { platforms } from "@/lib/social";
import { SocialConnection } from "../components/social-connection";

export default function ConnectionsPage() {
  return (
    <section className="project-workspace tiktok-workspace">
      <header className="project-heading">
        <h1>Connected accounts</h1>
        <Button asChild variant="outline">
          <Link href="/projects">Your projects</Link>
        </Button>
      </header>
      {platforms.map((platform) => (
        <SocialConnection key={platform} platform={platform} />
      ))}
    </section>
  );
}

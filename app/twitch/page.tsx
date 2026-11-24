import { Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { TwitchWorkspace } from "./workspace";

export const metadata = { title: "Twitch" };

export default function TwitchPage() {
  return (
    <section className="project-workspace">
      <header className="workspace-heading">
        <div>
          <h1>Twitch</h1>
          <p>Find clips, automate preparation, or review finished drafts.</p>
        </div>
      </header>
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        <TwitchWorkspace />
      </Suspense>
    </section>
  );
}

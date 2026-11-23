import type { ReactNode } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

export function PublishWorkspace({
  title,
  preview,
  children,
}: {
  title: string;
  preview: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="project-workspace publish-review-workspace">
      <header className="workspace-heading">
        <div>
          <h1>{title}</h1>
          <p>Review this saved export and its destination.</p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link href="/calendar">Publishing</Link>
        </Button>
      </header>
      <div className="publish-layout">
        <aside className="publish-preview" aria-label="Export preview">
          {preview}
        </aside>
        <div className="publish-details">{children}</div>
      </div>
    </section>
  );
}

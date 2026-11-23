import Link from "next/link";
import { PromptSettings } from "../components/prompt-settings";

export default function SettingsPage() {
  return (
    <section className="project-workspace">
      <header className="workspace-heading">
        <div>
          <h1>Settings</h1>
          <p>Manage the instructions used to create and review clips.</p>
        </div>
      </header>
      <PromptSettings />
      <nav
        aria-label="Legal"
        className="flex flex-wrap gap-4 border-t pt-4 text-sm text-muted-foreground"
      >
        <Link href="/privacy">Privacy policy</Link>
        <Link href="/terms">Terms of service</Link>
        <Link href="/data-deletion">Data deletion</Link>
      </nav>
    </section>
  );
}

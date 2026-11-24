import Link from "next/link";
import { Button } from "@/components/ui/button";

export function PublishingNavigation({
  current,
}: {
  current: "posts" | "accounts";
}) {
  return (
    <nav aria-label="Publishing sections" className="flex flex-wrap gap-2">
      <Button variant={current === "posts" ? "secondary" : "outline"} asChild>
        <Link
          href="/calendar"
          aria-current={current === "posts" ? "page" : undefined}
        >
          Posts
        </Link>
      </Button>
      <Button
        variant={current === "accounts" ? "secondary" : "outline"}
        asChild
      >
        <Link
          href="/publishing/accounts"
          aria-current={current === "accounts" ? "page" : undefined}
        >
          Accounts
        </Link>
      </Button>
    </nav>
  );
}

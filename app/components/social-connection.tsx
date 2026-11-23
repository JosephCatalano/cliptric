"use client";

import { useEffect, useState } from "react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldDescription,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, message } from "@/lib/client-api";
import {
  platformNames,
  platformRoot,
  type Platform,
  type SocialConnection as Connection,
} from "@/lib/social";

type Profile = {
  label?: string;
  username?: string;
  channel?: { title: string };
  creator_nickname?: string;
  creator_username?: string;
};

export function SocialConnection({ platform }: { platform: Platform }) {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [profileErrors, setProfileErrors] = useState<Record<string, string>>(
    {},
  );
  const [pageId, setPageId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const root = platformRoot(platform),
    name = platformNames[platform];
  const needsPage = platform === "facebook" || platform === "instagram";

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const value = await api<Connection>(root, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setConnection(value);
        setError("");
        if (
          new URLSearchParams(window.location.search).get(platform) ===
          "cancelled"
        )
          setNotice(`${name} connection was cancelled.`);
        const results = await Promise.all(
          value.accounts.map(async (account) => {
            try {
              const profile = await api<Profile>(
                `${root}/creator?account_id=${account.account_id}`,
                { signal: controller.signal },
              );
              const label =
                profile.label ??
                profile.channel?.title ??
                (profile.username
                  ? `@${profile.username}`
                  : profile.creator_username
                    ? `${profile.creator_nickname} (@${profile.creator_username})`
                    : account.label);
              return { id: account.account_id, label, error: "" };
            } catch (error) {
              return {
                id: account.account_id,
                label: account.label,
                error: message(error),
              };
            }
          }),
        );
        if (controller.signal.aborted) return;
        setLabels(
          Object.fromEntries(results.map((item) => [item.id, item.label])),
        );
        setProfileErrors(
          Object.fromEntries(results.map((item) => [item.id, item.error])),
        );
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      }
    }
    void load();
    return () => controller.abort();
  }, [root, platform, name, reload]);

  async function change(accountId?: string) {
    setBusy(true);
    setError("");
    try {
      if (accountId) {
        const result = await api<{ warning?: string }>(
          `${root}/disconnect?account_id=${accountId}`,
          { method: "POST" },
        );
        setNotice(result.warning ?? `${name} account disconnected.`);
        setReload((value) => value + 1);
      } else {
        const query =
          needsPage && pageId
            ? `?${new URLSearchParams({ page_id: pageId })}`
            : "";
        const result = await api<{ url: string }>(`${root}/connect${query}`, {
          method: "POST",
        });
        window.location.assign(result.url);
      }
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>{name}</CardTitle>
          {connection && <Badge variant={connection.accounts.length > 0 ? "success" : "secondary"}>
            {connection.accounts.length ? `${connection.accounts.length} connected` : connection.configured ? "Available" : "Setup required"}
          </Badge>}
        </div>
        <CardDescription>
          {platform === "facebook"
            ? "Connect Facebook Pages you manage. Page Reels are public."
            : `Connect one or more ${name} accounts and choose the destination for each clip.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {notice ? <p role="status">{notice}</p> : null}
        {!connection ? (
          <p role="status">
            {error ? "Account connections unavailable." : "Loading accounts..."}
          </p>
        ) : (
          <>
            {!connection.configured ? (
              <details className="rounded-lg border p-3 text-sm">
                <summary className="cursor-pointer font-medium">Administrator setup</summary>
                <p className="mt-2 text-muted-foreground">Set up the {name} integration using the Cliptric README, then restart the API.</p>
              </details>
            ) : null}
            {platform === "tiktok" && !connection.audited ? (
              <p>
                Test mode requires a private account and Only me visibility.
                Public posting requires TikTok approval.
              </p>
            ) : null}
            {platform === "x" ? (
              <p>
                X publishing requires developer API access and available API
                credits.
              </p>
            ) : null}
            {!connection.accounts.length ? (
              <p>No accounts connected.</p>
            ) : (
              <ul className="flex flex-col gap-3">
                {connection.accounts.map((account) => (
                  <li key={account.account_id} className="flex flex-col gap-2">
                    <div className="flex items-center justify-between gap-3">
                      <span>
                        {labels[account.account_id] ||
                          account.label ||
                          `Account ${account.account_id.slice(0, 10)}`}
                      </span>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => void change(account.account_id)}
                      >
                        Disconnect
                      </Button>
                    </div>
                    {profileErrors[account.account_id] ? (
                      <p role="status">{profileErrors[account.account_id]}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            <FieldGroup>
              {needsPage && connection.configured ? (
                <Field>
                  <FieldLabel htmlFor={`${platform}-page-id`}>
                    Facebook Page ID
                    {platform === "instagram" ? " linked to Instagram" : ""}
                  </FieldLabel>
                  <Input
                    id={`${platform}-page-id`}
                    value={pageId}
                    inputMode="numeric"
                    pattern="[0-9]{1,32}"
                    maxLength={32}
                    onChange={(event) => setPageId(event.target.value)}
                    disabled={busy}
                  />
                  <FieldDescription>
                    Enter the numeric Page ID and authorize that Page during
                    Meta login. Each Instagram destination needs its own linked
                    Page.
                  </FieldDescription>
                </Field>
              ) : null}
              <div className="flex flex-wrap gap-3">
                <Button
                  disabled={
                    busy ||
                    !connection.configured ||
                    (platform === "facebook" &&
                      !/^[0-9]{1,32}$/.test(pageId)) ||
                    (needsPage && !!pageId && !/^[0-9]{1,32}$/.test(pageId))
                  }
                  onClick={() => void change()}
                >
                  {connection.accounts.length
                    ? "Connect another account"
                    : `Connect ${name}`}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => setReload((value) => value + 1)}
                >
                  Refresh accounts
                </Button>
              </div>
            </FieldGroup>

          </>
        )}
      </CardContent>
    </Card>
  );
}

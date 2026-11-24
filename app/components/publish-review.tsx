"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCwIcon } from "lucide-react";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api, message } from "@/lib/client-api";
import {
  platformNames,
  platformRoot,
  type Platform,
  type SocialConnection,
} from "@/lib/social";
import { TikTokComposer } from "./tiktok-composer";
import { YouTubeComposer } from "./youtube-composer";
import { InstagramComposer } from "./instagram-composer";
import { SocialComposer } from "./social-composer";

export function PublishReview({
  platform,
  exportId,
  scheduledAt = "",
  initialAccount = "",
}: {
  platform: Platform;
  exportId: string;
  scheduledAt?: string;
  initialAccount?: string;
}) {
  const [connection, setConnection] = useState<SocialConnection | null>(null);
  const [selected, setSelected] = useState(initialAccount);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api<SocialConnection>(platformRoot(platform), { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        setConnection(value);
        setError("");
        setSelected(
          (current) =>
            current ||
            (value.accounts.length === 1 ? value.accounts[0].account_id : ""),
        );
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(message(error));
      });
    return () => controller.abort();
  }, [platform, reload]);
  const accountId = connection?.accounts.some(
    (account) => account.account_id === selected,
  )
    ? selected
    : "";
  const destination = (
    <section
      className="flex min-w-0 flex-col gap-3 border-b pb-4"
      aria-label="Destination"
    >
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {!connection ? (
        <p role="status">
          {error
            ? "Account review unavailable."
            : `Loading ${platformNames[platform]} accounts...`}
        </p>
      ) : (
        <Field>
          <FieldLabel htmlFor="publishing-account">
            {platformNames[platform]} destination account
          </FieldLabel>
          <NativeSelect
            id="publishing-account"
            value={accountId}
            onChange={(event) => setSelected(event.target.value)}
          >
            <NativeSelectOption value="">Choose an account</NativeSelectOption>
            {connection.accounts.map((account) => (
              <NativeSelectOption
                key={account.account_id}
                value={account.account_id}
              >
                {account.label || `Account ${account.account_id.slice(0, 10)}`}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          <FieldDescription>
            Changing accounts clears the previous review and approval.{" "}
            <Link href="/publishing/accounts">Manage connected accounts</Link>.
          </FieldDescription>
        </Field>
      )}
      <Button
        variant="ghost"
        size="sm"
        className="self-start"
        onClick={() => setReload((value) => value + 1)}
      >
        <RefreshCwIcon data-icon="inline-start" /> Refresh accounts
      </Button>
      {connection && selected && !accountId ? (
        <p role="status">
          The previous destination is disconnected. Reconnect it to check its
          saved post, or choose another account for a new review.
        </p>
      ) : null}
      {connection && !connection.accounts.length ? (
        <p>Connect an account to review and publish this clip.</p>
      ) : null}
    </section>
  );
  return (
    <>
      {!accountId && (
        <section className="project-workspace publish-review-workspace">
          <header className="workspace-heading">
            <div>
              <h1>Choose a destination</h1>
              <p>
                Connect or select a {platformNames[platform]} account to review
                this export.
              </p>
            </div>
          </header>
          {destination}
          <Button asChild variant="outline" className="self-start">
            <Link href="/publishing/accounts">Manage accounts</Link>
          </Button>
        </section>
      )}
      {accountId ? (
        platform === "youtube" ? (
          <YouTubeComposer
            key={accountId}
            exportId={exportId}
            scheduledAt={scheduledAt}
            accountId={accountId}
            destination={destination}
          />
        ) : platform === "tiktok" ? (
          <TikTokComposer
            key={accountId}
            exportId={exportId}
            scheduledAt={scheduledAt}
            accountId={accountId}
            destination={destination}
          />
        ) : platform === "instagram" ? (
          <InstagramComposer
            key={accountId}
            exportId={exportId}
            scheduledAt={scheduledAt}
            accountId={accountId}
            destination={destination}
          />
        ) : (
          <SocialComposer
            key={accountId}
            platform={platform}
            exportId={exportId}
            scheduledAt={scheduledAt}
            accountId={accountId}
            destination={destination}
          />
        )
      ) : null}
    </>
  );
}

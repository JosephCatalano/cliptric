"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
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
  return (
    <>
      <section className="project-workspace">
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
              <NativeSelectOption value="">
                Choose an account
              </NativeSelectOption>
              {connection.accounts.map((account) => (
                <NativeSelectOption
                  key={account.account_id}
                  value={account.account_id}
                >
                  {account.label ||
                    `Account ${account.account_id.slice(0, 10)}`}
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <FieldDescription>
              Changing accounts clears the previous review and approval.{" "}
              <Link href="/connections">Manage connected accounts</Link>.
            </FieldDescription>
          </Field>
        )}
        <Button
          variant="outline"
          onClick={() => setReload((value) => value + 1)}
        >
          Refresh accounts
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
      {accountId ? (
        platform === "youtube" ? (
          <YouTubeComposer
            key={accountId}
            exportId={exportId}
            scheduledAt={scheduledAt}
            accountId={accountId}
          />
        ) : platform === "tiktok" ? (
          <TikTokComposer
            key={accountId}
            exportId={exportId}
            scheduledAt={scheduledAt}
            accountId={accountId}
          />
        ) : platform === "instagram" ? (
          <InstagramComposer
            key={accountId}
            exportId={exportId}
            scheduledAt={scheduledAt}
            accountId={accountId}
          />
        ) : (
          <SocialComposer
            key={accountId}
            platform={platform}
            exportId={exportId}
            scheduledAt={scheduledAt}
            accountId={accountId}
          />
        )
      ) : null}
    </>
  );
}

"use client";
import { useEffect, useId, useState, type RefObject } from "react";
import Link from "next/link";
import { api, message } from "@/lib/client-api";
import {
  platforms,
  platformNames,
  platformRoot,
  type Platform,
  type SocialConnection,
} from "@/lib/social";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
  EmptyContent,
} from "@/components/ui/empty";

export function PublishChooser({
  exportId,
  title,
  disabled = false,
  open: controlledOpen,
  onOpenChange,
  returnFocus,
}: {
  exportId: string;
  title: string;
  disabled?: boolean;
  open?: boolean;
  onOpenChange?(value: boolean): void;
  returnFocus?: RefObject<HTMLButtonElement | null>;
}) {
  const id = useId();
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = onOpenChange ?? setInternalOpen;
  const [platform, setPlatform] = useState<Platform>("youtube");
  const [connection, setConnection] = useState<SocialConnection | null>(null);
  const [account, setAccount] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    api<SocialConnection>(platformRoot(platform), { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        setConnection(value);
        setAccount(
          value.accounts.length === 1 ? value.accounts[0].account_id : "",
        );
        setError("");
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(message(error));
      });
    return () => controller.abort();
  }, [platform, open, refresh]);
  const validAccount = connection?.accounts.some(
    (value) => value.account_id === account,
  )
    ? account
    : "";
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) {
          setConnection(null);
          setAccount("");
          setError("");
        }
      }}
    >
      {controlledOpen === undefined && <DialogTrigger asChild>
        <Button type="button" variant="outline" disabled={disabled}>
          Publish
        </Button>
      </DialogTrigger>}
      <DialogContent onCloseAutoFocus={(event) => {
        if (returnFocus?.current) {
          event.preventDefault();
          returnFocus.current.focus();
        }
      }}>
        <DialogHeader>
          <DialogTitle>Choose a destination</DialogTitle>
          <DialogDescription>
            {title}. Review this exact clip and approve publishing on the next
            screen.
          </DialogDescription>
        </DialogHeader>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor={`${id}-platform`}>Platform</FieldLabel>
            <NativeSelect
              id={`${id}-platform`}
              value={platform}
              onChange={(event) => {
                setPlatform(event.target.value as Platform);
                setConnection(null);
                setAccount("");
                setError("");
              }}
            >
              {platforms.map((value) => (
                <NativeSelectOption key={value} value={value}>
                  {platformNames[value]}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
          {!connection && !error ? (
            <Skeleton
              className="h-14"
              aria-label="Loading connected accounts"
            />
          ) : null}
          {connection?.accounts.length ? (
            <Field>
              <FieldLabel htmlFor={`${id}-account`}>Account</FieldLabel>
              <NativeSelect
                id={`${id}-account`}
                value={validAccount}
                onChange={(event) => setAccount(event.target.value)}
              >
                <NativeSelectOption value="">
                  Choose an account
                </NativeSelectOption>
                {connection.accounts.map((value) => (
                  <NativeSelectOption
                    key={value.account_id}
                    value={value.account_id}
                  >
                    {value.label || `Account ${value.account_id.slice(0, 10)}`}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
          ) : connection ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>No connected accounts</EmptyTitle>
                <EmptyDescription>
                  Connect a {platformNames[platform]} account to continue.
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button asChild variant="outline">
                  <Link href="/publishing/accounts">Connect an account</Link>
                </Button>
              </EmptyContent>
            </Empty>
          ) : null}
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>
                {error}{" "}
                <Button
                  type="button"
                  variant="link"
                  onClick={() => {
                    setError("");
                    setConnection(null);
                    setRefresh((value) => value + 1);
                  }}
                >
                  Retry accounts
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
          {validAccount && !error ? (
            <Button asChild>
              <Link
                href={`/publish/${platform}/${encodeURIComponent(exportId)}?${new URLSearchParams({ account_id: validAccount })}`}
              >
                Continue to review
              </Link>
            </Button>
          ) : (
            <Button disabled>Continue to review</Button>
          )}
        </FieldGroup>
      </DialogContent>
    </Dialog>
  );
}

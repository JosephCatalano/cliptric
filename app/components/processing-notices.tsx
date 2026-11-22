"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { confirmWorkspaceNavigation } from "./use-unsaved-edits";
import Link from "next/link";
import { BellIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import { useWorkspaceNavigation } from "./workspace-navigation";
import { api, ApiError, message } from "@/lib/client-api";
import { active, deliverable, type Project } from "@/lib/projects";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";

function completionText(project: Project) {
  const ready = project.items.filter(deliverable).length;
  if (project.status === "ready")
    return ready
      ? `${ready} ${ready === 1 ? "clip is" : "clips are"} ready to review`
      : "processing finished; no clips were selected";
  if (project.status === "partial")
    return `${ready} clips ready; review the unfinished clips`;
  return `processing ${project.status}`;
}

export function ProcessingNotices() {
  const path = usePathname();
  const { locked } = useWorkspaceNavigation();
  const router = useRouter();
  const legal = path === "/terms" || path === "/privacy";
  const pending = useRef(new Map<string, Project>());
  const [count, setCount] = useState(0);
  const [notices, setNotices] = useState<Project[]>([]);
  const [desktop, setDesktop] = useState(false);
  const [error, setError] = useState("");
  const [permissionNote, setPermissionNote] = useState("");
  const [checkingPermission, setCheckingPermission] = useState(false);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    Promise.resolve().then(() => {
      try {
        setDesktop(localStorage.getItem("cliptric:desktop-alerts") === "true");
      } catch {
        setPermissionNote(
          "Desktop alert preference cannot be saved in this browser.",
        );
      }
    });
  }, []);
  useEffect(() => {
    if (legal) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    function announce(project: Project) {
      pending.current.delete(project.id);
      setNotices((previous) =>
        [project, ...previous.filter((value) => value.id !== project.id)].slice(
          0,
          3,
        ),
      );
      if (
        desktop &&
        "Notification" in window &&
        Notification.permission === "granted"
      ) {
        try {
          const notification = new Notification(project.title, {
            body: completionText(project),
            tag: project.id,
          });
          notification.onclick = () => {
            window.focus();
            if (confirmWorkspaceNavigation())
              router.push(`/projects/${project.id}`);
          };
        } catch (error) {
          setPermissionNote(
            `Desktop alert could not be shown: ${message(error)}. In-app updates remain available.`,
          );
        }
      }
    }
    function track(event: Event) {
      const { project, generation } = (
        event as CustomEvent<{ project: Project; generation: boolean }>
      ).detail;
      if (active(project.status)) pending.current.set(project.id, project);
      else if (generation || pending.current.has(project.id)) announce(project);
      setCount(pending.current.size);
    }
    window.addEventListener("cliptric:project-processing", track);
    async function poll() {
      try {
        const reported = await api<Project[]>(
          "/api/projects?status=processing&limit=50",
          { signal: controller.signal },
        );
        const values = reported.filter((project) => active(project.status));
        if (controller.signal.aborted) return;
        const current = new Set(values.map((project) => project.id));
        const missing = [...pending.current.keys()].filter(
          (id) => !current.has(id),
        );
        const finished = await Promise.allSettled(
          missing.map((id) =>
            api<Project>(`/api/projects/${id}`, { signal: controller.signal }),
          ),
        );
        if (controller.signal.aborted) return;
        let failed = "";
        finished.forEach((result, index) => {
          if (result.status === "rejected") {
            if (
              result.reason instanceof ApiError &&
              result.reason.status === 404
            )
              pending.current.delete(missing[index]);
            else failed = message(result.reason);
            return;
          }
          const project = result.value;
          if (active(project.status)) return;
          announce(project);
        });
        for (const project of values) pending.current.set(project.id, project);
        setCount(values.length);
        setConnected(true);
        setError(failed);
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 8000);
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener("cliptric:project-processing", track);
    };
  }, [desktop, legal, router]);
  async function toggleDesktop() {
    setCheckingPermission(true);
    try {
      if (!("Notification" in window)) {
        setPermissionNote(
          "Desktop alerts are unavailable. In-app updates still work while Cliptric is open.",
        );
        return;
      }
      const enabled =
        !desktop &&
        (Notification.permission === "granted" ||
          (await Notification.requestPermission()) === "granted");
      localStorage.setItem("cliptric:desktop-alerts", String(enabled));
      setDesktop(enabled);
      setPermissionNote(
        enabled
          ? "Desktop alerts enabled while Cliptric is open."
          : "Desktop alerts off. In-app updates remain enabled.",
      );
    } catch (error) {
      setPermissionNote(message(error));
    } finally {
      setCheckingPermission(false);
    }
  }
  if (legal) return null;
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Notifications: ${notices.length} new updates${count ? `, ${count} projects processing` : ""}${error ? ", status unavailable" : ""}`}
          className="relative"
        >
          <BellIcon />
          {notices.length || error ? (
            <Badge
              variant={error ? "destructive" : "secondary"}
              className="absolute -top-1 -right-1"
            >
              {error ? "!" : notices.length}
            </Badge>
          ) : null}
        </Button>
      </SheetTrigger>
      <span className="sr-only" role="status">
        {notices[0]
          ? `${notices[0].title}: ${completionText(notices[0])}`
          : count
            ? `${count} projects processing`
            : ""}
      </span>
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Notifications</SheetTitle>
          <SheetDescription>
            Recent processing updates while Cliptric is open.
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 px-4 pb-6">
          {count ? (
            <Button asChild variant="outline">
              <Link
                href="/projects"
                aria-disabled={locked}
                onClick={(event) => {
                  if (locked) event.preventDefault();
                }}
              >
                {count} projects processing
              </Link>
            </Button>
          ) : null}
          {!connected && !error ? (
            <p role="status">Checking processing...</p>
          ) : null}
          {error ? (
            <Alert>
              <AlertDescription>
                Cannot refresh processing status. {error} Reconnecting
                automatically.
              </AlertDescription>
            </Alert>
          ) : null}
          {notices.map((project) => (
            <Alert key={project.id}>
              <AlertDescription>
                <p>
                  {project.title}: {completionText(project)}.
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button asChild variant="link">
                    <Link
                      href={`/projects/${project.id}`}
                      aria-disabled={locked}
                      onClick={(event) => {
                        if (locked) event.preventDefault();
                      }}
                    >
                      Open project
                    </Link>
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() =>
                      setNotices((values) =>
                        values.filter((value) => value.id !== project.id),
                      )
                    }
                  >
                    Dismiss
                  </Button>
                </div>
              </AlertDescription>
            </Alert>
          ))}
          {connected && !notices.length && !error ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>No new updates</EmptyTitle>
                <EmptyDescription>
                  Completed jobs will appear here.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : null}
          {locked ? (
            <p>Save or discard studio edits before opening another project.</p>
          ) : null}
          <Button
            variant="outline"
            disabled={checkingPermission}
            onClick={() => void toggleDesktop()}
          >
            {checkingPermission
              ? "Checking permission..."
              : desktop
                ? "Turn desktop alerts off"
                : "Enable desktop alerts"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Alerts require the browser and backend to stay available.
          </p>
          {permissionNote ? <p role="status">{permissionNote}</p> : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

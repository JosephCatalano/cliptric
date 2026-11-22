"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { confirmWorkspaceNavigation } from "./use-unsaved-edits";
import Link from "next/link";
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
    <section
      className="flex flex-col gap-2 px-4 py-2"
      aria-label="Processing updates"
    >
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        {count ? (
          <Link href="/projects">
            {count} {count === 1 ? "project" : "projects"} processing
          </Link>
        ) : (
          <span>
            {connected ? "Processing updates enabled" : "Checking processing…"}
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={checkingPermission}
          onClick={() => void toggleDesktop()}
        >
          {checkingPermission
            ? "Checking permission…"
            : desktop
              ? "Turn desktop alerts off"
              : "Enable desktop alerts"}
        </Button>
        {permissionNote ? <span role="status">{permissionNote}</span> : null}
      </div>
      {error ? (
        <Alert>
          <AlertDescription>
            Processing updates unavailable: {error} Reconnecting automatically.{" "}
            <Link href="/projects">Open Projects</Link>
          </AlertDescription>
        </Alert>
      ) : null}
      {notices.map((project) => (
        <Alert key={project.id}>
          <AlertDescription>
            <span role="status">
              {project.title}: {completionText(project)}.
            </span>
            <div className="flex flex-wrap gap-2">
              <Button asChild variant="link">
                <Link href={`/projects/${project.id}`}>Open project</Link>
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
    </section>
  );
}

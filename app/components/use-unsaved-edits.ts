"use client";
import { useEffect } from "react";

const checked = new WeakSet<Event>();

export function confirmWorkspaceNavigation() {
  return window.dispatchEvent(
    new Event("cliptric:before-navigate", { cancelable: true }),
  );
}

export function useUnsavedEdits(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => event.preventDefault();
    const programmatic = (event: Event) => {
      if (event.defaultPrevented || checked.has(event)) return;
      checked.add(event);
      if (!window.confirm("Leave this page with unsaved clip edits?"))
        event.preventDefault();
    };
    const navigate = (event: MouseEvent) => {
      if (
        event.defaultPrevented ||
        checked.has(event) ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const anchor =
        event.target instanceof Element
          ? event.target.closest<HTMLAnchorElement>("a[href]")
          : null;
      if (
        !anchor ||
        anchor.hasAttribute("download") ||
        anchor.target === "_blank"
      )
        return;
      const target = new URL(anchor.href, location.href);
      if (
        target.pathname.startsWith("/api/") &&
        (target.searchParams.get("download") === "true" ||
          target.pathname.endsWith("/bundle"))
      )
        return;
      if (
        target.origin !== location.origin ||
        (target.pathname === location.pathname &&
          target.search === location.search)
      )
        return;
      checked.add(event);
      if (!window.confirm("Leave this page with unsaved clip edits?")) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", unload);
    window.addEventListener("cliptric:before-navigate", programmatic);
    document.addEventListener("click", navigate, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("cliptric:before-navigate", programmatic);
      document.removeEventListener("click", navigate, true);
    };
  }, [dirty]);
}

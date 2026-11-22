"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const NavigationContext = createContext<{
  locked: boolean;
  setLocked: (locked: boolean) => void;
} | null>(null);

export function WorkspaceNavigationProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    if (!locked) return;
    const protect = (event: Event) => event.preventDefault();
    window.addEventListener("cliptric:before-navigate", protect);
    return () =>
      window.removeEventListener("cliptric:before-navigate", protect);
  }, [locked]);
  const value = useMemo(() => ({ locked, setLocked }), [locked]);
  return (
    <NavigationContext.Provider value={value}>
      {children}
    </NavigationContext.Provider>
  );
}

export function useWorkspaceNavigation() {
  const value = useContext(NavigationContext);
  if (!value) throw new Error("Workspace navigation requires its provider.");
  return value;
}

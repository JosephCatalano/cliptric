"use client";

import {
  createContext,
  useContext,
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

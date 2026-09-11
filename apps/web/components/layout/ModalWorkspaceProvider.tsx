"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  activeModuleFromLocation,
  buildModuleHref,
  parseModuleHref,
} from "@/lib/modal-workspace/parse-route";
import type { ActiveModule } from "@/lib/modal-workspace/types";

type ModalWorkspaceContextValue = {
  activeModule: ActiveModule | null;
  openModule: (href: string) => void;
  closeModule: () => void;
  isOpen: boolean;
  mainScrollRef: React.RefObject<HTMLElement>;
};

const ModalWorkspaceContext = createContext<ModalWorkspaceContextValue | null>(
  null
);

export function ModalWorkspaceProvider({ children }: { children: ReactNode }) {
  const [activeModule, setActiveModule] = useState<ActiveModule | null>(null);
  const mainScrollRef = useRef<HTMLElement>(null!);
  const savedScrollTop = useRef(0);

  const saveScrollPosition = useCallback(() => {
    if (mainScrollRef.current) {
      savedScrollTop.current = mainScrollRef.current.scrollTop;
    }
  }, []);

  const restoreScrollPosition = useCallback(() => {
    if (mainScrollRef.current) {
      mainScrollRef.current.scrollTop = savedScrollTop.current;
    }
  }, []);

  const syncUrl = useCallback((module: ActiveModule | null) => {
    if (typeof window === "undefined") return;
    const nextHref = module ? buildModuleHref(module) : "/";
    const current = `${window.location.pathname}${window.location.search}`;
    if (current !== nextHref) {
      window.history.replaceState(window.history.state, "", nextHref);
    }
  }, []);

  const openModule = useCallback(
    (href: string) => {
      const next = parseModuleHref(href);
      saveScrollPosition();
      setActiveModule(next);
      syncUrl(next);
    },
    [saveScrollPosition, syncUrl]
  );

  const closeModule = useCallback(() => {
    setActiveModule(null);
    syncUrl(null);
    requestAnimationFrame(() => {
      restoreScrollPosition();
    });
  }, [restoreScrollPosition, syncUrl]);

  useEffect(() => {
    const module = activeModuleFromLocation(
      window.location.pathname,
      new URLSearchParams(window.location.search)
    );
    if (module) {
      setActiveModule(module);
      if (window.location.pathname !== "/") {
        syncUrl(module);
      }
    }
  }, [syncUrl]);

  const value = useMemo(
    () => ({
      activeModule,
      openModule,
      closeModule,
      isOpen: activeModule != null,
      mainScrollRef,
    }),
    [activeModule, openModule, closeModule]
  );

  return (
    <ModalWorkspaceContext.Provider value={value}>
      {children}
    </ModalWorkspaceContext.Provider>
  );
}

export function useModalWorkspace() {
  const ctx = useContext(ModalWorkspaceContext);
  if (!ctx) {
    throw new Error("useModalWorkspace must be used within ModalWorkspaceProvider");
  }
  return ctx;
}

"use client";

import { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import { activeModuleFromLocation } from "@/lib/modal-workspace/parse-route";
import { isModalPath } from "@/lib/modal-workspace/module-registry";
import { HomeDashboard } from "@/components/dashboard/HomeDashboard";
import { BottomNavigation } from "./BottomNavigation";
import { TopHeader } from "./TopHeader";
import { ModalWorkspaceShell } from "./ModalWorkspaceShell";
import { useModalWorkspace } from "./ModalWorkspaceProvider";

export function DashboardShell() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { isOpen, mainScrollRef, openModule } = useModalWorkspace();

  useEffect(() => {
    if (pathname === "/") return;
    if (!isModalPath(pathname)) return;

    const module = activeModuleFromLocation(pathname, searchParams);
    if (module) {
      openModule(
        `${module.path}${
          Object.keys(module.searchParams).length
            ? `?${new URLSearchParams(module.searchParams).toString()}`
            : ""
        }`
      );
      router.replace(
        `/?m=${encodeURIComponent(module.path)}${
          Object.keys(module.searchParams).length
            ? `&${new URLSearchParams(module.searchParams).toString()}`
            : ""
        }`
      );
    }
  }, [pathname, searchParams, openModule, router]);

  return (
    <div className="flex min-h-screen flex-col">
      <TopHeader />
      <main
        ref={mainScrollRef}
        className={cn("flex-1 overflow-auto pb-20", isOpen && "overflow-hidden")}
        aria-hidden={isOpen}
      >
        <div
          className={cn(
            "mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8",
            isOpen && "pointer-events-none select-none"
          )}
        >
          <HomeDashboard />
        </div>
      </main>
      <BottomNavigation />
      <ModalWorkspaceShell />
    </div>
  );
}

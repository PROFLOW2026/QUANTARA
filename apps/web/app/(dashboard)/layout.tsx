import { Suspense } from "react";
import { ModalWorkspaceProvider } from "@/components/layout/ModalWorkspaceProvider";
import { DashboardShell } from "@/components/layout/DashboardShell";

export default function DashboardLayout({
  children: _children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ModalWorkspaceProvider>
      <Suspense fallback={null}>
        <DashboardShell />
      </Suspense>
    </ModalWorkspaceProvider>
  );
}

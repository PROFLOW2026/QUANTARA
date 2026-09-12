import { BottomNavigation } from "./BottomNavigation";
import { TopHeader } from "./TopHeader";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <TopHeader />
      <main className="flex-1 overflow-auto pb-20">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</div>
      </main>
      <BottomNavigation />
    </div>
  );
}

import { Suspense } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { OptionsLabDashboard } from "@/components/options/OptionsLabDashboard";

export default function OptionsPage() {
  return (
    <>
      <PageHeader titleKey="home.view_options" />
      <Suspense fallback={null}>
        <OptionsLabDashboard />
      </Suspense>
    </>
  );
}

"use client";

import { PageHeader } from "@/components/layout/PageHeader";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import type { ReactNode } from "react";

export function ModuleFrame({
  embedded,
  titleKey,
  action,
  children,
}: ModuleProps & {
  titleKey: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <>
      {!embedded ? <PageHeader titleKey={titleKey} action={action} /> : null}
      {children}
    </>
  );
}

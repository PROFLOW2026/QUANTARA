"use client";

import { ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";
import { ModuleFrame } from "./ModuleFrame";

export default function ExperimentsModule({ embedded }: ModuleProps) {
  const { data: experiments, error, loading } = useModuleData(
    () => api.getExperiments(),
    []
  );

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  return (
    <ModuleFrame embedded={embedded} titleKey="experiments.title">
      {error ? (
        <div className="mb-4"><ErrorBanner message={error} /></div>
      ) : null}

      <Card>
        <CardContent className="pt-4">
          {!experiments?.length ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <p className="max-w-md text-sm text-muted">{t("experiments.empty")}</p>
              <p className="mt-2 max-w-lg text-xs text-muted/80">{t("experiments.empty_detail")}</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("common.name")}</TableHead>
                  <TableHead>{t("common.status")}</TableHead>
                  <TableHead>{t("experiments.instances")}</TableHead>
                  <TableHead>{t("common.period")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {experiments.map((exp) => (
                  <TableRow key={exp.id}>
                    <TableCell className="font-medium">{exp.name}</TableCell>
                    <TableCell><StatusBadge status={exp.status} /></TableCell>
                    <TableCell>{exp.instances_count}</TableCell>
                    <TableCell className="text-xs">
                      {exp.period_start && exp.period_end
                        ? `${formatDateTime(exp.period_start)} — ${formatDateTime(exp.period_end)}`
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </ModuleFrame>
  );
}

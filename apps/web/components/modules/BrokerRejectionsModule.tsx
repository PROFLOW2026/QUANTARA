"use client";

import { ErrorBanner } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState } from "@/components/ui/table";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { formatDateTime } from "@/lib/utils";
import { translateBrokerReason } from "@/lib/display-text";
import { ModuleFrame } from "./ModuleFrame";

export default function BrokerRejectionsModule({ embedded }: ModuleProps) {
  const { data, error, loading } = useModuleData(() => api.getBrokerRejections(200), []);

  const rows = data?.rejections ?? [];

  return (
    <ModuleFrame embedded={embedded} titleKey="nav.decisions">
      <Card>
        <CardHeader>
          <CardTitle>Broker Rejected Orders</CardTitle>
        </CardHeader>
        <CardContent>
          {error ? <div className="mb-4"><ErrorBanner message={error} /></div> : null}
          {loading ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : !rows.length ? (
            <EmptyState message="No broker rejections logged" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Asset</TableHead>
                  <TableHead>Qty</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r, i) => (
                  <TableRow key={`${r.timestamp}-${i}`}>
                    <TableCell className="text-xs">{formatDateTime(r.timestamp)}</TableCell>
                    <TableCell>{r.symbol}</TableCell>
                    <TableCell className="font-mono">{r.quantity}</TableCell>
                    <TableCell>{translateBrokerReason(r.reason)}</TableCell>
                    <TableCell className="max-w-xs truncate text-xs text-muted">
                      {translateBrokerReason(r.reason)}
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

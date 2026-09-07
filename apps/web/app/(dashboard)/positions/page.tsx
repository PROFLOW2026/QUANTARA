import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { DirectionBadge } from "@/components/trading/DirectionBadge";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPrice } from "@/lib/utils";

export default async function PositionsPage() {
  let positions = null;
  let error: string | null = null;

  try {
    positions = await api.getPositions("open");
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  return (
    <>
      <PageHeader titleKey="positions.title" />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <Card>
        <CardHeader><CardTitle>{t("positions.title")}</CardTitle></CardHeader>
        <CardContent>
          {!positions?.length ? (
            <EmptyState message={t("positions.empty")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("common.instrument")}</TableHead>
                  <TableHead>{t("common.direction")}</TableHead>
                  <TableHead>{t("positions.size")}</TableHead>
                  <TableHead>{t("positions.entry")}</TableHead>
                  <TableHead>{t("positions.current")}</TableHead>
                  <TableHead>{t("positions.sl_tp")}</TableHead>
                  <TableHead>{t("positions.pnl")}</TableHead>
                  <TableHead>{t("positions.duration")}</TableHead>
                  <TableHead>{t("common.strategy")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {positions.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell>{p.instrument}</TableCell>
                    <TableCell><DirectionBadge direction={p.direction} /></TableCell>
                    <TableCell className="font-mono">{p.size}</TableCell>
                    <TableCell>
                      <span className="font-mono">{formatPrice(p.entry_price)}</span>
                      <br />
                      <span className="text-xs text-muted">{formatDateTime(p.entry_time)}</span>
                    </TableCell>
                    <TableCell className="font-mono">{formatPrice(p.current_price)}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {p.stop_loss != null ? formatPrice(p.stop_loss) : "—"} /{" "}
                      {p.take_profit != null ? formatPrice(p.take_profit) : "—"}
                    </TableCell>
                    <TableCell>
                      <PnLDisplay value={p.unrealized_pnl} showPercent={p.unrealized_pnl_pct} size="sm" />
                    </TableCell>
                    <TableCell>{p.duration ?? "—"}</TableCell>
                    <TableCell className="text-xs">
                      {p.strategy_name ?? "—"}
                      {p.strategy_version && ` v${p.strategy_version}`}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}

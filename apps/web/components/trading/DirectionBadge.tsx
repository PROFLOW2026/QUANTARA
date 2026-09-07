import { Badge } from "@/components/ui/badge";
import { t } from "@/lib/i18n";

interface DirectionBadgeProps {
  direction: "LONG" | "SHORT" | string;
}

export function DirectionBadge({ direction }: DirectionBadgeProps) {
  const isLong = direction.toUpperCase() === "LONG";
  return (
    <Badge variant={isLong ? "success" : "danger"}>
      {isLong ? t("common.long") : t("common.short")}
    </Badge>
  );
}

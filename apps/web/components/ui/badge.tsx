import { cn } from "@/lib/utils";

type BadgeVariant = "default" | "success" | "danger" | "warning" | "info" | "muted" | "outline";

const variants: Record<BadgeVariant, string> = {
  default: "bg-info-bg text-info border-info-border",
  success: "bg-profit-bg text-profit border-profit-border",
  danger: "bg-loss-bg text-loss border-loss-border",
  warning: "bg-warning-bg text-warning border-warning-border",
  info: "bg-info-bg text-info border-info-border",
  muted: "bg-surface-inner text-foreground-secondary border-border-nested",
  outline: "bg-transparent text-foreground-secondary border-border",
};

export function Badge({
  className,
  variant = "default",
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { variant?: BadgeVariant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
        variants[variant],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}

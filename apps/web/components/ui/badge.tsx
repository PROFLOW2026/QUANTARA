import { cn } from "@/lib/utils";

type BadgeVariant = "default" | "success" | "danger" | "warning" | "muted" | "outline";

const variants: Record<BadgeVariant, string> = {
  default: "bg-accent/20 text-accent border-accent/30",
  success: "bg-profit/15 text-profit border-profit/30",
  danger: "bg-loss/15 text-loss border-loss/30",
  warning: "bg-warning/15 text-warning border-warning/30",
  muted: "bg-surface-elevated text-foreground-secondary border-border",
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

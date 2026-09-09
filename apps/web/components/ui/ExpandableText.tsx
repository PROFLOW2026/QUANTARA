"use client";

import { useId, useState } from "react";
import { t } from "@/lib/i18n";

export function ExpandableText({
  text,
  maxLength = 42,
}: {
  text: string;
  maxLength?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const controlId = useId();
  const trimmed = text.trim();
  const truncatable = trimmed.length > maxLength;

  if (!truncatable) {
    return <span className="break-words">{trimmed || "—"}</span>;
  }

  return (
    <div className="text-right">
      <button
        type="button"
        id={controlId}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        className="w-full text-right break-words hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
      >
        {expanded ? trimmed : `${trimmed.slice(0, maxLength)}…`}
      </button>
      {!expanded ? (
        <span className="mt-0.5 block text-xs text-muted">{t("common.show_full_reason")}</span>
      ) : null}
    </div>
  );
}

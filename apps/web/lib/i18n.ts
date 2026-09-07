import he from "@/messages/he.json";

type Messages = Record<string, unknown>;

function getNestedValue(obj: Messages, path: string): string | undefined {
  const keys = path.split(".");
  let current: unknown = obj;
  for (const key of keys) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Messages)[key];
  }
  return typeof current === "string" ? current : undefined;
}

export function t(key: string, params?: Record<string, string | number>): string {
  const value = getNestedValue(he as Messages, key) ?? key;
  if (!params) return value;
  return value.replace(/\{(\w+)\}/g, (_, name: string) =>
    String(params[name] ?? `{${name}}`)
  );
}

export function getLocale(): "he" {
  return "he";
}

export function getDir(): "rtl" {
  return "rtl";
}

import type { ActiveModule, ModuleSearchParams } from "./types";

export function parseModuleHref(href: string): ActiveModule {
  const [rawPath, query = ""] = href.split("?");
  const path = rawPath.startsWith("/") ? rawPath : `/${rawPath}`;
  const searchParams = Object.fromEntries(
    new URLSearchParams(query).entries()
  ) as ModuleSearchParams;
  return { path, searchParams };
}

export function buildModuleHref(module: ActiveModule): string {
  const params = new URLSearchParams();
  params.set("m", module.path);
  for (const [key, value] of Object.entries(module.searchParams)) {
    if (value) params.set(key, value);
  }
  const qs = params.toString();
  return qs ? `/?${qs}` : "/";
}

export function activeModuleFromLocation(
  pathname: string,
  searchParams: URLSearchParams
): ActiveModule | null {
  const modulePath = searchParams.get("m");
  if (modulePath) {
    const search: ModuleSearchParams = {};
    searchParams.forEach((value, key) => {
      if (key !== "m") search[key] = value;
    });
    return {
      path: modulePath.startsWith("/") ? modulePath : `/${modulePath}`,
      searchParams: search,
    };
  }

  if (pathname === "/") return null;

  const search: ModuleSearchParams = {};
  searchParams.forEach((value, key) => {
    search[key] = value;
  });
  return { path: pathname, searchParams: search };
}

export function matchModuleParams(
  pattern: RegExp,
  path: string
): Record<string, string> {
  const match = path.match(pattern);
  if (!match?.[1]) return {};

  if (pattern.source.startsWith("^\\/backtests\\/")) {
    return { id: match[1] };
  }
  return { slug: match[1] };
}

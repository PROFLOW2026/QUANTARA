import type { ComponentType } from "react";

export type ModuleSearchParams = Record<string, string>;

export type ActiveModule = {
  path: string;
  searchParams: ModuleSearchParams;
};

export type ModuleProps = {
  embedded?: boolean;
  searchParams?: ModuleSearchParams;
  params?: Record<string, string>;
};

export type ModuleDefinition = {
  titleKey: string;
  pattern?: RegExp;
  path?: string;
  load: () => Promise<{ default: ComponentType<ModuleProps> }>;
};

"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";

const STORAGE_KEY = "quantara-theme";

export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      storageKey={STORAGE_KEY}
      disableTransitionOnChange
      themes={["dark", "light"]}
    >
      {children}
    </NextThemesProvider>
  );
}

export { STORAGE_KEY };

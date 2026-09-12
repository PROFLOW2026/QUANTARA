import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "var(--color-background)",
        surface: "var(--color-surface)",
        "surface-elevated": "var(--color-surface-elevated)",
        "surface-chrome": "var(--color-surface-chrome)",
        border: "var(--color-border)",
        foreground: "var(--color-foreground)",
        "foreground-secondary": "var(--color-foreground-secondary)",
        muted: "var(--color-muted)",
        profit: "var(--color-profit)",
        loss: "var(--color-loss)",
        warning: "var(--color-warning)",
        accent: "var(--color-accent)",
        primary: "var(--color-primary)",
        "primary-foreground": "var(--color-primary-foreground)",
        overlay: "var(--color-overlay)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;

/** @type {import('next').NextConfig} */
try {
  require("../../scripts/load-env.cjs").loadRepoEnv();
} catch {
  // Full monorepo checkout only — Vercel uses project env vars directly.
}

const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_ENGINE_URL: process.env.NEXT_PUBLIC_ENGINE_URL,
    NEXT_PUBLIC_API_KEY: process.env.NEXT_PUBLIC_API_KEY,
  },
};

module.exports = nextConfig;
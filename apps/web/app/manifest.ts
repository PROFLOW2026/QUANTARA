import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "QUANTARA",
    short_name: "QUANTARA",
    description: "מסחר אלגוריתמי — סימולציה ובדיקות היסטוריות",
    start_url: "/",
    scope: "/",
    id: "/",
    display: "standalone",
    orientation: "portrait-primary",
    background_color: "#0a0e17",
    theme_color: "#0a0e17",
    dir: "rtl",
    lang: "he",
    icons: [
      {
        src: "/icons/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}

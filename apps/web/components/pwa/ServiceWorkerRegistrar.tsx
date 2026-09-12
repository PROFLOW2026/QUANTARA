"use client";

import { useEffect } from "react";

export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;

    let hadController = Boolean(navigator.serviceWorker.controller);

    navigator.serviceWorker
      .register("/sw.js", { updateViaCache: "none" })
      .catch(() => {
        /* non-fatal */
      });

    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!hadController) {
        hadController = true;
        return;
      }
      window.location.reload();
    });
  }, []);

  return null;
}

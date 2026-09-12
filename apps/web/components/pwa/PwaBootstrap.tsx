"use client";

import { useEffect } from "react";
import { initPwaInstallPromptCapture } from "@/lib/pwa/install-prompt-capture";
import { ServiceWorkerRegistrar } from "./ServiceWorkerRegistrar";

initPwaInstallPromptCapture();

export function PwaBootstrap() {
  useEffect(() => {
    initPwaInstallPromptCapture();
  }, []);

  return <ServiceWorkerRegistrar />;
}

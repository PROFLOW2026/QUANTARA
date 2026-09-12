"use client";

import { useCallback, useEffect, useState } from "react";
import {
  type InstallCapability,
  type InstallPromptOutcome,
  isStandaloneDisplay,
  resolveInstallCapability,
} from "./installability";
import {
  hasDeferredInstallPrompt,
  initPwaInstallPromptCapture,
  promptInstall,
  subscribeInstallPrompt,
} from "./install-prompt-capture";

function readEnvironment() {
  if (typeof window === "undefined") {
    return {
      displayModeStandalone: false,
      iosNavigatorStandalone: false,
      userAgent: "",
      hasDeferredPrompt: false,
    };
  }

  const iosStandalone =
    "standalone" in window.navigator &&
    Boolean((window.navigator as Navigator & { standalone?: boolean }).standalone);

  return {
    displayModeStandalone: window.matchMedia("(display-mode: standalone)").matches,
    iosNavigatorStandalone: iosStandalone,
    userAgent: window.navigator.userAgent,
    hasDeferredPrompt: hasDeferredInstallPrompt(),
  };
}

export function usePwaInstall() {
  const [capability, setCapability] = useState<InstallCapability>("unavailable");
  const [lastOutcome, setLastOutcome] = useState<InstallPromptOutcome | null>(null);

  const sync = useCallback(() => {
    setCapability(resolveInstallCapability(readEnvironment()));
  }, []);

  useEffect(() => {
    initPwaInstallPromptCapture();
    sync();

    const media = window.matchMedia("(display-mode: standalone)");
    const onMedia = () => sync();
    media.addEventListener("change", onMedia);
    window.addEventListener("appinstalled", sync);

    const unsubscribe = subscribeInstallPrompt(sync);

    return () => {
      media.removeEventListener("change", onMedia);
      window.removeEventListener("appinstalled", sync);
      unsubscribe();
    };
  }, [sync]);

  const install = useCallback(async () => {
    const outcome = await promptInstall();
    setLastOutcome(outcome);
    sync();
    return outcome;
  }, [sync]);

  const installed = isStandaloneDisplay(readEnvironment());

  return { capability, install, installed, lastOutcome, refresh: sync };
}

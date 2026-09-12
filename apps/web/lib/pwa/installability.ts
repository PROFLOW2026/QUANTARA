export type InstallCapability =
  | "installed"
  | "prompt_available"
  | "manual_ios"
  | "unavailable";

export interface InstallEnvironmentSnapshot {
  readonly displayModeStandalone: boolean;
  readonly iosNavigatorStandalone: boolean;
  readonly userAgent: string;
  readonly hasDeferredPrompt: boolean;
}

export function isStandaloneDisplay(env: {
  readonly displayModeStandalone: boolean;
  readonly iosNavigatorStandalone: boolean;
}): boolean {
  return env.displayModeStandalone || env.iosNavigatorStandalone;
}

export function isIosInstallManual(userAgent: string): boolean {
  const ua = userAgent.toLowerCase();
  const iosDevice = /iphone|ipad|ipod/.test(ua);
  const ipadDesktopUa = ua.includes("macintosh") && ua.includes("mobile");
  return iosDevice || ipadDesktopUa;
}

export function resolveInstallCapability(
  env: InstallEnvironmentSnapshot
): InstallCapability {
  if (isStandaloneDisplay(env)) return "installed";
  if (env.hasDeferredPrompt) return "prompt_available";
  if (isIosInstallManual(env.userAgent)) return "manual_ios";
  return "unavailable";
}

export type InstallPromptOutcome = "accepted" | "dismissed" | "error" | "unavailable";

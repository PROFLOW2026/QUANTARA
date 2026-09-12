type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

type Listener = () => void;

let deferredPrompt: BeforeInstallPromptEvent | null = null;
const listeners = new Set<Listener>();

export function initPwaInstallPromptCapture(): void {
  if (typeof window === "undefined") return;

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event as BeforeInstallPromptEvent;
    listeners.forEach((listener) => listener());
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    listeners.forEach((listener) => listener());
  });
}

export function hasDeferredInstallPrompt(): boolean {
  return deferredPrompt !== null;
}

export function subscribeInstallPrompt(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function promptInstall(): Promise<
  "accepted" | "dismissed" | "unavailable"
> {
  if (!deferredPrompt) return "unavailable";
  const prompt = deferredPrompt;
  deferredPrompt = null;
  try {
    await prompt.prompt();
    const choice = await prompt.userChoice;
    return choice.outcome;
  } catch {
    return "unavailable";
  }
}

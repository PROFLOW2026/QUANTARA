"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  buildLiveStreamUrl,
  parseLiveMarkEvent,
  resolveBrowserStreamBaseUrl,
  type LiveMarkMap,
} from "@/lib/live-mark-stream";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

export function useLiveMarkStream() {
  const [liveMarks, setLiveMarks] = useState<LiveMarkMap>(new Map());
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const retryRef = useRef(0);
  const latestRef = useRef<LiveMarkMap>(new Map());
  const visibleRef = useRef(true);

  const applyMarks = useCallback((marks: LiveMarkMap) => {
    latestRef.current = marks;
    if (visibleRef.current) {
      setLiveMarks(new Map(marks));
    }
  }, []);

  useEffect(() => {
    const onVisibility = () => {
      visibleRef.current = document.visibilityState === "visible";
      if (visibleRef.current && latestRef.current.size > 0) {
        setLiveMarks(new Map(latestRef.current));
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    const baseUrl = resolveBrowserStreamBaseUrl();
    if (!baseUrl) {
      return;
    }

    let cancelled = false;
    let reconnectTimer: number | undefined;

    const connect = async () => {
      if (cancelled) {
        return;
      }
      try {
        const tokenRes = await fetch("/api/engine/stream-token");
        if (!tokenRes.ok) {
          throw new Error("stream token unavailable");
        }
        const { token } = (await tokenRes.json()) as { token?: string };
        if (!token || cancelled) {
          return;
        }

        sourceRef.current?.close();
        const es = new EventSource(buildLiveStreamUrl(baseUrl, token));
        sourceRef.current = es;

        es.onopen = () => {
          retryRef.current = 0;
          setConnected(true);
        };

        es.onmessage = (event) => {
          const marks = parseLiveMarkEvent(event.data);
          if (marks) {
            applyMarks(marks);
          }
        };

        es.onerror = () => {
          setConnected(false);
          es.close();
          sourceRef.current = null;
          if (cancelled) {
            return;
          }
          const delay = Math.min(
            RECONNECT_BASE_MS * 2 ** retryRef.current,
            RECONNECT_MAX_MS
          );
          retryRef.current += 1;
          reconnectTimer = window.setTimeout(() => {
            void connect();
          }, delay);
        };
      } catch {
        setConnected(false);
        if (!cancelled) {
          reconnectTimer = window.setTimeout(() => {
            void connect();
          }, RECONNECT_MAX_MS);
        }
      }
    };

    void connect();

    return () => {
      cancelled = true;
      if (reconnectTimer !== undefined) {
        window.clearTimeout(reconnectTimer);
      }
      sourceRef.current?.close();
      sourceRef.current = null;
      setConnected(false);
    };
  }, [applyMarks]);

  return { liveMarks, connected };
}

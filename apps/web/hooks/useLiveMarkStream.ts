"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  buildLiveStreamUrl,
  parseLiveMarkEvent,
  resolveRuntimeStreamBaseUrl,
  type LiveMarkMap,
} from "@/lib/live-mark-stream";

const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS = 60000;

export function useLiveMarkStream() {
  const [liveMarks, setLiveMarks] = useState<LiveMarkMap>(new Map());
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const retryRef = useRef(0);
  const latestRef = useRef<LiveMarkMap>(new Map());
  const visibleRef = useRef(true);
  const connectGenRef = useRef(0);

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
    let cancelled = false;
    let reconnectTimer: number | undefined;

    const closeSource = () => {
      sourceRef.current?.close();
      sourceRef.current = null;
    };

    const connect = async () => {
      if (cancelled || !visibleRef.current) {
        return;
      }

      const generation = ++connectGenRef.current;

      try {
        const baseUrl = await resolveRuntimeStreamBaseUrl();
        if (!baseUrl || cancelled || generation !== connectGenRef.current) {
          return;
        }

        const tokenRes = await fetch("/api/engine/stream-token");
        if (!tokenRes.ok || cancelled || generation !== connectGenRef.current) {
          throw new Error("stream token unavailable");
        }
        const { token } = (await tokenRes.json()) as { token?: string };
        if (!token || cancelled || generation !== connectGenRef.current) {
          return;
        }

        closeSource();
        const es = new EventSource(buildLiveStreamUrl(baseUrl, token));
        sourceRef.current = es;

        es.onopen = () => {
          if (cancelled || generation !== connectGenRef.current) {
            es.close();
            return;
          }
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
          if (generation !== connectGenRef.current) {
            return;
          }
          setConnected(false);
          es.close();
          sourceRef.current = null;
          if (cancelled || !visibleRef.current) {
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
        if (cancelled || generation !== connectGenRef.current) {
          return;
        }
        setConnected(false);
        if (!cancelled && visibleRef.current) {
          reconnectTimer = window.setTimeout(() => {
            void connect();
          }, RECONNECT_MAX_MS);
        }
      }
    };

    const onVisibility = () => {
      visibleRef.current = document.visibilityState === "visible";
      if (!visibleRef.current) {
        connectGenRef.current += 1;
        if (reconnectTimer !== undefined) {
          window.clearTimeout(reconnectTimer);
          reconnectTimer = undefined;
        }
        closeSource();
        setConnected(false);
        return;
      }
      if (!sourceRef.current) {
        void connect();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    void connect();

    return () => {
      cancelled = true;
      connectGenRef.current += 1;
      document.removeEventListener("visibilitychange", onVisibility);
      if (reconnectTimer !== undefined) {
        window.clearTimeout(reconnectTimer);
      }
      closeSource();
      setConnected(false);
    };
  }, [applyMarks]);

  return { liveMarks, connected };
}

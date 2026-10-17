"use client";
/**
 * React hook: persistent /ws/dashboard connection with auto-reconnect
 * (exponential backoff) and keepalive pings. Calls `onEvent` for every event.
 */
import { useEffect, useRef, useState } from "react";
import { getToken, wsBase } from "./api";
import type { HubEvent } from "./types";

export type SocketState = "connecting" | "open" | "closed";

export function useHubSocket(onEvent: (ev: HubEvent) => void): SocketState {
  const [state, setState] = useState<SocketState>("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let attempt = 0;
    let stopped = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let pingTimer: ReturnType<typeof setInterval> | undefined;

    const connect = () => {
      const token = getToken();
      if (!token || stopped) return;
      setState("connecting");
      ws = new WebSocket(`${wsBase()}/ws/dashboard?token=${encodeURIComponent(token)}`);
      ws.onopen = () => {
        attempt = 0;
        setState("open");
        pingTimer = setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("PING"), 25000);
      };
      ws.onmessage = (m) => {
        try {
          handler.current(JSON.parse(m.data) as HubEvent);
        } catch {
          /* ignore malformed */
        }
      };
      ws.onclose = () => {
        clearInterval(pingTimer);
        setState("closed");
        if (stopped) return;
        const delay = Math.min(30000, 1000 * 2 ** attempt++);
        retryTimer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      clearInterval(pingTimer);
      ws?.close();
    };
  }, []);

  return state;
}

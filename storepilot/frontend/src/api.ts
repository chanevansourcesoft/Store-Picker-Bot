import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "./types";

/** POST/GET helper. Throws Error(detail) when the backend refuses. */
export async function api<T = unknown>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { detail?: string }).detail || `Request failed (${res.status})`);
  return data as T;
}

export type Link = "connecting" | "live" | "lost";

/** Live snapshot over the WebSocket, reconnecting forever with backoff. */
export function useSnapshot() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [link, setLink] = useState<Link>("connecting");
  const lastMessage = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { retry = 0; };
      ws.onmessage = (msg) => {
        const data = JSON.parse(msg.data);
        if (data.type === "state") {
          lastMessage.current = Date.now();
          setSnapshot(data.data);
          setLink("live");
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setLink("lost");
        retry = Math.min(retry + 1, 5);
        timer = window.setTimeout(connect, retry * 1000);
      };
      ws.onerror = () => ws?.close();
    };
    connect();

    // No message for 3 s -> treat the link as lost even if the socket looks open
    const watchdog = window.setInterval(() => {
      if (lastMessage.current && Date.now() - lastMessage.current > 3000) setLink("lost");
    }, 1000);

    return () => {
      closed = true;
      window.clearTimeout(timer);
      window.clearInterval(watchdog);
      ws?.close();
    };
  }, []);

  return { snapshot, link };
}

export function fmtTime(iso: string | null | undefined) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function fmtDuration(sec: number | null | undefined) {
  if (sec === null || sec === undefined) return "—";
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function missionDuration(m: { confirmed_at: string | null; finished_at: string | null }) {
  if (!m.confirmed_at) return null;
  const end = m.finished_at ? new Date(m.finished_at).getTime() : Date.now();
  return (end - new Date(m.confirmed_at).getTime()) / 1000;
}

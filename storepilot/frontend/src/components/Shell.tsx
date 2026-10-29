import { useEffect, useState } from "react";
import { ClipboardList, Clapperboard, Gauge, Map as MapIcon, OctagonX, Server, ShieldAlert } from "lucide-react";
import { api, type Link } from "../api";
import type { Snapshot } from "../types";
import { Logo } from "./Logo";
import { Dot } from "./ui";

export type Tab = "ops" | "order" | "mission" | "videos" | "system";

export const TABS: { id: Tab; label: string; icon: typeof Gauge }[] = [
  { id: "ops", label: "Operations", icon: Gauge },
  { id: "order", label: "Order", icon: ClipboardList },
  { id: "mission", label: "Live mission", icon: MapIcon },
  { id: "videos", label: "Real robot", icon: Clapperboard },
  { id: "system", label: "System", icon: Server },
];

function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return <span className="num text-[15px] font-semibold text-ink">{now.toLocaleTimeString([], { hour12: false })}</span>;
}

function linkColor(state: string) {
  if (["connected", "live", "ready", "ok"].includes(state)) return "var(--color-ok)";
  if (["simulated", "stub", "degraded", "offline"].includes(state)) return "var(--color-warn)";
  if (["unknown"].includes(state)) return "var(--color-dim)";
  return "var(--color-err)";
}

export function TopBar({ snap, link, toast }: { snap: Snapshot | null; link: Link; toast: (t: string, tone?: "ok" | "err" | "warn") => void }) {
  const [busy, setBusy] = useState(false);
  const robot = snap?.robot;
  const c = snap?.connections;

  async function stop() {
    setBusy(true);
    try {
      const r = await api<{ success: boolean; message: string }>("/api/robot/stop", {});
      toast(r.success ? `Robot acknowledged STOP: ${r.message}` : `${r.message}`, r.success ? "warn" : "err");
    } catch (e) {
      toast(`STOP request failed: ${(e as Error).message}. Use the hardware emergency stop.`, "err");
    } finally {
      setBusy(false);
    }
  }

  const stopped = robot?.stopped === true;

  async function resume() {
    try {
      const r = await api<{ success: boolean; message: string }>("/api/robot/resume", {});
      toast(r.success ? `Resumed: ${r.message}` : r.message, r.success ? "ok" : "err");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  const stopLabel = stopped ? "STOPPED" : robot?.stop_pending || busy ? "STOPPING…" : "STOP";

  return (
    <header className="flex h-[68px] shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
      <div className="flex min-w-0 items-center gap-2.5">
        <Logo className="h-10 w-10" />
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[17px] font-bold tracking-tight">StorePilot <span className="text-brand">AI</span></div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-dim">Dark store picking robot · NVIDIA Nemotron</div>
        </div>
      </div>

      <div className="ml-auto flex items-center gap-3">
        {snap && (
          <span className={`whitespace-nowrap rounded-md px-2 py-1 text-[11px] font-bold uppercase tracking-[0.1em] ring-1 ${
            snap.mode === "mock" ? "bg-warn/15 text-warn ring-warn/40" : "bg-ok/10 text-ok ring-ok/40"}`}>
            {snap.mode === "mock" ? "Simulation" : "Live robot"}
          </span>
        )}
        <div className="flex flex-col gap-1 text-[11px] font-semibold text-muted">
          <span className="flex items-center gap-1.5"><Dot color={link === "live" ? "var(--color-ok)" : "var(--color-err)"} />Server</span>
          <span className="flex items-center gap-1.5"><Dot color={c ? linkColor(c.jetson.state) : "var(--color-dim)"} />Robot</span>
        </div>
        <div className="flex flex-col gap-1 text-[11px] font-semibold text-muted">
          <span className="flex items-center gap-1.5"><Dot color={c ? linkColor(c.telemetry.state) : "var(--color-dim)"} />Telemetry</span>
          <span className="flex items-center gap-1.5"><Dot color={c ? linkColor(c.ai.state) : "var(--color-dim)"} />AI</span>
        </div>
        <Clock />
        {stopped && (
          <button onClick={resume} className="h-[52px] rounded-lg border border-line-strong bg-raised px-3 text-[14px] font-bold text-ink active:bg-line">
            Resume
          </button>
        )}
        <button onClick={stop} disabled={!snap || link !== "live" || busy}
          className={`flex h-[52px] min-w-[118px] flex-col items-center justify-center rounded-lg border-2 px-3 font-extrabold tracking-[0.1em] transition-colors disabled:opacity-50 ${
            stopped ? "border-err bg-err/15 text-err" : "border-[#ff8a87] bg-err text-white active:bg-[#c93633]"}`}
          aria-label="Stop the robot">
          <span className="flex items-center gap-1.5 text-[16px]"><OctagonX size={20} aria-hidden />{stopLabel}</span>
          {snap && !snap.safety.stop_connected ? (
            <span className="text-[9px] font-bold tracking-wider opacity-90">PLANNER ONLY</span>
          ) : snap && !snap.safety.stop_verified ? (
            <span className="text-[9px] font-bold tracking-wider opacity-90">NOT VERIFIED</span>
          ) : null}
        </button>
      </div>
    </header>
  );
}

export function SimBanner({ snap }: { snap: Snapshot }) {
  if (snap.mode === "mock") {
    return (
      <div className="sim-stripe flex shrink-0 items-center justify-center gap-2 border-b border-warn/30 py-1 text-[12px] font-semibold text-warn">
        <ShieldAlert size={14} aria-hidden />
        SIMULATION MODE — robot, map and telemetry are simulated, not from the Jetson
      </div>
    );
  }
  return null;
}

export function TabNav({ tab, setTab, badges }: { tab: Tab; setTab: (t: Tab) => void; badges: Partial<Record<Tab, boolean>> }) {
  return (
    <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-panel px-3">
      {TABS.map(({ id, label, icon: Icon }) => {
        const active = id === tab;
        return (
          <button key={id} onClick={() => setTab(id)}
            className={`relative flex h-12 shrink-0 items-center gap-2 px-4 text-[14px] font-semibold transition-colors ${
              active ? "text-ink" : "text-dim hover:text-muted"}`}
            aria-current={active ? "page" : undefined}>
            <Icon size={18} aria-hidden />
            {label}
            {badges[id] && <span className="h-2 w-2 rounded-full bg-err" aria-label="needs attention" />}
            {active && <span className="absolute inset-x-2 bottom-0 h-[3px] rounded-t bg-brand" />}
          </button>
        );
      })}
    </nav>
  );
}

import { useState } from "react";
import { AlertTriangle, BatteryMedium, CheckSquare, Flag, Hand, Layers, LocateFixed, MapPin, Navigation, Play, Save, Square, X } from "lucide-react";
import { api } from "../api";
import type { Snapshot } from "../types";
import { RosMap, type Picked, type PickMode } from "./RosMap";
import { Button, Dot, Panel, StateBadge } from "./ui";

type Toast = (t: string, tone?: "ok" | "err" | "warn") => void;
type Pending =
  | { kind: "goal"; p: Picked }
  | { kind: "pose"; p: Picked }
  | { kind: "station"; name: string }
  | { kind: "map"; name: string };

const STATIONS = ["home", "shelf_A", "shelf_B", "shelf_C", "customer"];
const deg = (t: number) => Math.round(((t * 180) / Math.PI + 360) % 360);

/** Live driving over rosbridge: RViz-like map, set position, send goals, stations, map switching. */
export function LiveDrive({ snap, toast }: { snap: Snapshot; toast: Toast }) {
  const r = snap.robot;
  const [mode, setMode] = useState<PickMode>("view");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const connected = snap.connections.jetson.state === "connected";
  const stations = snap.map.stations ?? {};
  const label = (s: string) => snap.map.labels[s] ?? s;
  const driving = r.goal?.status === "accepted" || r.goal?.status === "executing";

  const blocker = !connected ? "Robot disconnected"
    : r.stopped ? "Robot stopped: press Resume in the top bar first"
      : r.nav2_active !== true ? "Nav2 is paused: press Start Nav2"
        : !r.localized ? "Robot not localized: use Set position"
          : !snap.drive.motion_enabled ? "Driving disabled on this dashboard (STOREPILOT_ENABLE_MOTION=0)"
            : snap.mission?.status === "running" ? "A mission is running" : "";

  async function run(path: string, body: unknown, ok: string) {
    setBusy(true);
    try {
      await api(path, body);
      toast(ok, "ok");
      setPending(null);
      setMode("view");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {/* status strip */}
      <div className="grid grid-cols-4 gap-2">
        <Chip label="Nav2" ok={r.nav2_active === true} text={r.nav2_active === true ? "Active" : r.nav2_active === false ? "Paused" : "Unknown"} />
        <Chip label="Localized" ok={!!r.localized} text={r.localized ? "Yes" : "No"} />
        <Chip label="Battery" ok={r.battery_v != null && r.battery_v >= 11.1}
          text={r.battery_v != null ? `${r.battery_v.toFixed(2)} V` : "Unavailable"} icon={<BatteryMedium size={13} />} />
        <Chip label="Speed" ok text={r.speed != null ? `${r.speed.toFixed(2)} m/s` : "Unavailable"} />
      </div>

      <Panel title={<>Live map · {r.map_name ?? "unknown map"}</>} right={<StateBadge state={r.state} />} bodyClass="p-2">
        <RosMap snap={snap} mode={mode}
          onPick={(p) => setPending(mode === "pose" ? { kind: "pose", p } : { kind: "goal", p })} />
        <div className="mt-2 grid grid-cols-3 gap-2 px-1">
          {([
            ["view", "View / pan", Hand],
            ["pose", "Set position", LocateFixed],
            ["goal", "Send goal", Flag],
          ] as const).map(([m, text, Icon]) => (
            <button key={m} onClick={() => { setMode(m); setPending(null); }}
              className={`flex min-h-12 items-center justify-center gap-2 rounded-lg border text-[15px] font-semibold ${
                mode === m ? (m === "pose" ? "border-plan bg-plan/15 text-plan" : m === "goal" ? "border-ok bg-ok/15 text-ok" : "border-info bg-info/15 text-info")
                  : "border-line-strong text-muted active:bg-raised"}`}>
              <Icon size={18} aria-hidden />{text}
            </button>
          ))}
        </div>
        <p className="px-1 pt-2 text-[13px] text-dim">
          {mode === "view" ? "Drag to pan, buttons to zoom. Like RViz, nothing is sent in this mode."
            : mode === "pose" ? "Press where the robot really is, drag toward where it faces, release. Does not move the robot."
              : "Press the destination, drag toward the final heading, release. You will confirm before it drives."}
        </p>
        {r.goal && (
          <p className="px-1 pt-1 text-[13px] text-muted">
            Goal {r.goal.station ? label(r.goal.station) : `(${r.goal.x.toFixed(2)}, ${r.goal.y.toFixed(2)})`}: {r.goal.status ?? "sent"}
            {r.goal.distance != null && driving ? ` · ${r.goal.distance.toFixed(2)} m straight-line` : ""}
          </p>
        )}
      </Panel>

      {r.nav2_active === false && !r.stopped && (
        <Button tone="primary" busy={busy} onClick={() => run("/api/nav2/start", {}, "Nav2 started")}>
          <Play size={18} aria-hidden />Start Nav2 (resume localization + navigation)
        </Button>
      )}
      {blocker && (r.nav2_active !== false || r.stopped) && (
        <p className="flex items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[14px] text-[#ffd59a]">
          <AlertTriangle size={16} aria-hidden />Driving not possible: {blocker}
        </p>
      )}

      <Panel title="Drive to a station" icon={<Navigation size={15} />}
        right={<button onClick={() => setSaveOpen(!saveOpen)} className="flex min-h-9 items-center gap-1.5 rounded-md border border-line px-2.5 text-[13px] font-semibold text-muted active:bg-raised">
          <Save size={14} aria-hidden />{saveOpen ? "Done" : "Save stations"}</button>}>
        <div className="grid grid-cols-5 gap-2">
          {STATIONS.map((s) => {
            const saved = !!stations[s];
            return saveOpen ? (
              <button key={s} disabled={!r.localized || busy}
                onClick={() => run("/api/stations", { name: s }, `${label(s)} saved here`)}
                className="flex min-h-14 flex-col items-center justify-center rounded-lg border border-warn/50 bg-warn/10 text-[13px] font-semibold text-warn active:bg-warn/20 disabled:opacity-40">
                <Save size={16} aria-hidden />Here = {label(s)}
              </button>
            ) : (
              <button key={s} disabled={!saved || !!blocker} onClick={() => setPending({ kind: "station", name: s })}
                className="flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-lg border border-line-strong bg-raised text-[14px] font-semibold text-ink active:bg-line disabled:opacity-35">
                <MapPin size={16} aria-hidden className={saved ? "text-warn" : "text-dim"} />{label(s)}
                {!saved && <span className="text-[10px] font-medium text-dim">not saved</span>}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-[12px] text-dim">
          {saveOpen ? "Drive or push the robot to the spot, make sure it is localized, then tap the station to save its current position for this map."
            : "Stations are saved per map on this Pi. Use “Save stations” to record them."}
        </p>
      </Panel>

      <Panel title="Environment (map on the Jetson)" icon={<Layers size={15} />}>
        <div className="flex flex-wrap gap-2">
          {(snap.ros?.maps ?? []).map((name) => (
            <button key={name} disabled={busy || driving || name === r.map_name} onClick={() => setPending({ kind: "map", name })}
              className={`min-h-11 rounded-lg border px-3 text-[14px] font-semibold ${
                name === r.map_name ? "border-ok bg-ok/10 text-ok" : "border-line-strong text-muted active:bg-raised"} disabled:cursor-default`}>
              {name}
            </button>
          ))}
        </div>
        <p className="mt-2 text-[12px] text-dim">Switching loads the map with Nav2's map_server. Afterwards set the robot position again.</p>
      </Panel>

      {pending && (
        <Sheet pending={pending} snap={snap} busy={busy} blocker={blocker} label={label} onClose={() => setPending(null)}
          onConfirm={(areaClear) => {
            if (pending.kind === "pose") return run("/api/drive/initial_pose", pending.p, "Position set");
            if (pending.kind === "goal") return run("/api/drive/goal", { ...pending.p, area_clear: areaClear }, "Goal sent: robot is driving");
            if (pending.kind === "station") return run(`/api/drive/station/${pending.name}`, { area_clear: areaClear }, `Driving to ${label(pending.name)}`);
            return run("/api/maps/load", { name: pending.name }, `Map ${pending.name} loaded: now set the position`);
          }} />
      )}
    </div>
  );
}

function Chip({ label, text, ok, icon }: { label: string; text: string; ok: boolean; icon?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-panel px-3 py-2">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-dim">{icon}{label}</div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[15px] font-semibold">
        <Dot color={ok ? "var(--color-ok)" : "var(--color-warn)"} />{text}
      </div>
    </div>
  );
}

function Sheet({ pending, snap, busy, blocker, label, onClose, onConfirm }: {
  pending: Pending; snap: Snapshot; busy: boolean; blocker: string; label: (s: string) => string;
  onClose: () => void; onConfirm: (areaClear: boolean) => void;
}) {
  const [clear, setClear] = useState(false);
  const moves = pending.kind === "goal" || pending.kind === "station";
  const title = pending.kind === "pose" ? "Set robot position here?"
    : pending.kind === "goal" ? "Drive the robot here?"
      : pending.kind === "station" ? `Drive to ${label(pending.name)}?`
        : `Load map ${pending.name}?`;
  const detail = pending.kind === "pose" || pending.kind === "goal"
    ? `x ${pending.p.x.toFixed(2)} m · y ${pending.p.y.toFixed(2)} m · heading ${deg(pending.p.theta)}°`
    : pending.kind === "map" ? "The robot will need its position set again on the new map." : "Uses the saved station pose for this map.";
  return (
    <div className="fixed inset-x-0 bottom-[76px] z-40 border-t-2 border-line-strong bg-panel p-4 shadow-[0_-12px_40px_rgba(0,0,0,.6)]" role="dialog">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold">{title}</h3>
          <p className="num mt-0.5 text-[14px] text-muted">{detail}</p>
        </div>
        <button onClick={onClose} aria-label="Cancel" className="flex h-11 w-11 items-center justify-center rounded-lg border border-line text-muted"><X size={20} /></button>
      </div>
      {moves && (
        <>
          {blocker ? (
            <p className="mt-3 text-[14px] font-medium text-warn">Cannot drive: {blocker}</p>
          ) : (
            <button onClick={() => setClear(!clear)}
              className={`mt-3 flex min-h-14 w-full items-center gap-3 rounded-xl border-2 px-4 text-left text-[15px] font-semibold ${
                clear ? "border-ok bg-ok/10 text-ink" : "border-line-strong bg-raised text-muted"}`}>
              {clear ? <CheckSquare size={24} className="shrink-0 text-ok" aria-hidden /> : <Square size={24} className="shrink-0" aria-hidden />}
              Path is clear and nobody is in the way · hardware E-stop in reach
            </button>
          )}
          {!snap.drive.stop_verified && (
            <p className="mt-2 text-[12px] text-dim">Dashboard STOP cancels the goal and pauses Nav2; it is not physically verified yet.</p>
          )}
        </>
      )}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Button onClick={onClose}>Cancel</Button>
        <Button tone="primary" busy={busy} disabled={moves && (!clear || !!blocker)} onClick={() => onConfirm(clear)}>
          {pending.kind === "pose" ? "Set position" : pending.kind === "map" ? "Load map" : "Drive"}
        </Button>
      </div>
    </div>
  );
}

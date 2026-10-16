import { useRef, useState } from "react";
import { AlertTriangle, BatteryMedium, CheckSquare, Download, Flag, Hand, Layers, LocateFixed, MapPin, Navigation, Play, Route, Save, Square, Trash2, Upload, X } from "lucide-react";
import { api } from "../api";
import type { Snapshot } from "../types";
import { RosMap, type Picked, type PickMode } from "./RosMap";
import { Button, Dot, Panel, StateBadge } from "./ui";

type Toast = (t: string, tone?: "ok" | "err" | "warn") => void;
type Pending =
  | { kind: "goal"; p: Picked }
  | { kind: "pose"; p: Picked }
  | { kind: "station"; name: string }
  | { kind: "route" }
  | { kind: "map"; name: string };
type Check = { level: "ok" | "warn" | "bad"; reason: string; clearance: number | null };
type Draft = { x: number; y: number; theta: number; station?: string; label: string; check?: Check };

async function checkPoint(x: number, y: number): Promise<Check | undefined> {
  try {
    const [c] = await api<Check[]>("/api/drive/check", { points: [{ x, y }] });
    return c;
  } catch {
    return undefined;
  }
}

const STATIONS = ["home", "shelf_A", "shelf_B", "shelf_C", "customer"];
const deg = (t: number) => Math.round(((t * 180) / Math.PI + 360) % 360);

/** Live driving over rosbridge: RViz-like map, set position, send goals, stations, map switching. */
export function LiveDrive({ snap, toast }: { snap: Snapshot; toast: Toast }) {
  const r = snap.robot;
  const [mode, setMode] = useState<PickMode>("view");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [draft, setDraft] = useState<Draft[]>([]);
  const [showCostmap, setShowCostmap] = useState(true);
  const [pendingCheck, setPendingCheck] = useState<Check | undefined>();
  const fileRef = useRef<HTMLInputElement>(null);
  const badStops = draft.map((d, i) => (d.check?.level === "bad" ? i : -1)).filter((i) => i >= 0);

  function addStop(d: Draft) {
    setDraft((list) => [...list, d]);
    checkPoint(d.x, d.y).then((check) => setDraft((list) => list.map((x) => (x === d ? { ...x, check } : x))));
  }

  function pickGoal(p: Picked) {
    setPendingCheck(undefined);
    setPending({ kind: "goal", p });
    checkPoint(p.x, p.y).then(setPendingCheck);
  }

  async function exportStations() {
    const data = await api("/api/stations/export");
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "storepilot-stations.json" });
    a.click();
    URL.revokeObjectURL(url);
  }

  async function importStations(file: File) {
    try {
      const data = JSON.parse(await file.text());
      const r = await api<{ imported: number }>("/api/stations/import", { data });
      toast(`Imported ${r.imported} station(s)`, "ok");
    } catch (e) {
      toast(`Import failed: ${(e as Error).message}`, "err");
    }
  }
  const route = snap.route;
  const running = !!route?.busy;
  const connected = snap.connections.jetson.state === "connected";
  const stations = snap.map.stations ?? {};
  const label = (s: string) => snap.map.labels[s] ?? s;
  const driving = running || r.goal?.status === "accepted" || r.goal?.status === "executing";

  const blocker = !connected ? "Robot disconnected"
    : snap.alerts.some((a) => a.text.includes("LiDAR scan")) ? "No LiDAR scan: check the LiDAR (power / battery)"
    : r.odom_bad ? `Odometry broken (${r.odom_bad}): restart navigation on the System page`
    : r.stopped ? "Robot stopped: press Resume in the top bar first"
      : r.nav2_active == null ? "Nav2 is not running on the Jetson (start navigation.launch.py)"
      : r.nav2_active !== true ? "Nav2 is paused: press Start Nav2"
        : !r.localized ? "Robot not localized: use Set position"
          : !snap.drive.motion_enabled ? "Driving disabled on this dashboard (STOREPILOT_ENABLE_MOTION=0)"
            : snap.mission?.status === "running" ? "A mission is running"
              : r.loc_quality != null && r.loc_quality < 0.3 ? "Localization poor: press Auto-align or Set position" : "";

  async function run(path: string, body: unknown, ok: string): Promise<boolean> {
    setBusy(true);
    try {
      await api(path, body);
      toast(ok, "ok");
      setPending(null);
      setMode("view");
      return true;
    } catch (e) {
      toast((e as Error).message, "err");
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {/* status strip */}
      <div className="grid grid-cols-4 gap-2">
        <Chip label="Nav2" ok={r.nav2_active === true} text={r.nav2_active === true ? "Active" : r.nav2_active === false ? "Paused" : "Unknown"} />
        <Chip label="Localization" ok={(r.loc_quality ?? 0) >= 0.55}
          text={!r.localized ? "Not set" : r.loc_quality == null ? "Checking…"
            : `${Math.round(r.loc_quality * 100)}% ${r.loc_quality >= 0.55 ? "Good" : r.loc_quality >= 0.3 ? "Fair" : "Poor"}`} />
        <Chip label="Battery" ok={r.battery_v != null && r.battery_v >= 11.1}
          text={r.battery_v != null ? `${r.battery_v.toFixed(2)} V` : "Unavailable"} icon={<BatteryMedium size={13} />} />
        <Chip label="Speed" ok text={r.speed != null ? `${r.speed.toFixed(2)} m/s` : "Unavailable"} />
      </div>

      <Panel title={<>Live map · {r.map_name ?? "unknown map"}</>} right={<StateBadge state={r.state} />} bodyClass="p-2">
        <RosMap snap={snap} mode={mode} draft={draft} showCostmap={showCostmap} badPoints={badStops}
          onPick={(p) => mode === "route"
            ? addStop({ ...p, label: `point ${draft.length + 1}` })
            : mode === "pose" ? setPending({ kind: "pose", p }) : pickGoal(p)} />
        {snap.ros?.costmap && (
          <label className="flex min-h-10 items-center gap-2 px-1 pt-1 text-[13px] text-muted">
            <input type="checkbox" checked={showCostmap} onChange={(e) => setShowCostmap(e.target.checked)} className="h-5 w-5 accent-[#a78bfa]" />
            Show Nav2 costmap (purple = inflation, red = lethal) and robot footprint (teal)
          </label>
        )}
        <div className="mt-2 grid grid-cols-4 gap-2 px-1">
          {([
            ["view", "View", Hand],
            ["pose", "Set position", LocateFixed],
            ["goal", "Send goal", Flag],
            ["route", "Route", Route],
          ] as const).map(([m, text, Icon]) => (
            <button key={m} onClick={() => { setMode(m); setPending(null); }}
              className={`flex min-h-12 items-center justify-center gap-1.5 rounded-lg border text-[14px] font-semibold ${
                mode === m ? (m === "pose" ? "border-plan bg-plan/15 text-plan" : m === "goal" ? "border-ok bg-ok/15 text-ok"
                  : m === "route" ? "border-warn bg-warn/15 text-warn" : "border-info bg-info/15 text-info")
                  : "border-line-strong text-muted active:bg-raised"}`}>
              <Icon size={18} aria-hidden />{text}
            </button>
          ))}
        </div>
        {r.localized && (
          <div className="px-1 pt-2">
            <Button className="w-full" busy={busy} disabled={driving}
              onClick={() => run("/api/drive/align", {}, "Auto-aligned to the walls")}>
              <LocateFixed size={18} aria-hidden />Auto-align to walls (robot does not move)
            </Button>
          </div>
        )}
        <p className="px-1 pt-2 text-[13px] text-dim">
          {mode === "view" ? "Drag to pan, buttons to zoom. Like RViz, nothing is sent in this mode."
            : mode === "pose" ? "Press roughly where the robot is, drag toward where it faces, release. The Pi then fits the LiDAR to the walls within 50 cm / 16°. Does not move the robot."
              : mode === "route" ? "Tap (and drag for heading) each stop in order. Add saved stations below. Then Start route."
                : "Press the destination, drag toward the final heading, release. You will confirm before it drives."}
        </p>
        {r.goal && (
          <p className="px-1 pt-1 text-[13px] text-muted">
            Goal {r.goal.station ? label(r.goal.station) : `(${r.goal.x.toFixed(2)}, ${r.goal.y.toFixed(2)})`}: {r.goal.status ?? "sent"}
            {r.goal.distance != null && driving ? ` · ${r.goal.distance.toFixed(2)} m straight-line` : ""}
          </p>
        )}
      </Panel>

      {route?.route && (
        <RouteStatus snap={snap} busy={busy} onCancel={() => run("/api/drive/route/cancel", {}, "Route cancelled")} />
      )}

      {(mode === "route" || draft.length > 0) && !running && (
        <Panel title={`New route · ${draft.length} stop${draft.length === 1 ? "" : "s"}`} icon={<Route size={15} />}
          right={draft.length > 0 && (
            <button onClick={() => setDraft([])} className="flex min-h-9 items-center gap-1.5 rounded-md border border-line px-2.5 text-[13px] font-semibold text-muted active:bg-raised">
              <Trash2 size={14} aria-hidden />Clear</button>)}>
          {draft.length === 0 ? (
            <p className="text-[14px] text-dim">No stops yet: tap the map, or add a saved station.</p>
          ) : (
            <ol className="flex flex-wrap gap-2">
              {draft.map((d, i) => (
                <li key={i} title={d.check?.reason}
                  className={`flex min-h-10 items-center gap-2 rounded-lg border pl-3 pr-1 text-[14px] font-semibold ${
                    d.check?.level === "bad" ? "border-err/60 bg-err/10 text-[#ffb4b2]" : "border-warn/40 bg-warn/10 text-[#ffd59a]"}`}>
                  <span className="num">{i + 1}.</span>{d.station ? label(d.station) : `(${d.x.toFixed(2)}, ${d.y.toFixed(2)})`}
                  {d.check?.level === "bad" && <AlertTriangle size={14} aria-label={d.check.reason} />}
                  <button aria-label={`Remove stop ${i + 1}`} onClick={() => setDraft(draft.filter((_, j) => j !== i))}
                    className="flex h-8 w-8 items-center justify-center rounded-md text-muted active:bg-raised"><X size={16} /></button>
                </li>
              ))}
            </ol>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {STATIONS.filter((s) => stations[s]).map((s) => (
              <button key={s} onClick={() => addStop({ ...stations[s], station: s, label: label(s) })}
                className="min-h-10 rounded-lg border border-line-strong px-3 text-[13px] font-semibold text-muted active:bg-raised">
                + {label(s)}
              </button>
            ))}
          </div>
          {badStops.length > 0 && (
            <p className="mt-2 text-[13px] text-[#ffb4b2]">
              Stop {badStops.map((i) => i + 1).join(", ")}: {draft[badStops[0]].check?.reason} Remove or move it.
            </p>
          )}
          <Button tone="primary" className="mt-3 w-full" disabled={draft.length === 0 || !!blocker || badStops.length > 0}
            onClick={() => setPending({ kind: "route" })}>
            <Play size={18} aria-hidden />Start route ({draft.length} stop{draft.length === 1 ? "" : "s"})
          </Button>
        </Panel>
      )}

      {route && (
        <Panel title="Drive options" bodyClass="grid grid-cols-2 gap-2">
          <Toggle on={route.settings.auto_align} label="Auto-align at stops"
            hint="Fit LiDAR to the walls whenever the robot stands still"
            onChange={(v) => run("/api/drive/settings", { auto_align: v }, `Auto-align ${v ? "on" : "off"}`)} />
          <Toggle on={route.settings.guard} label="Localization guard"
            hint="If the fit drops while driving: stop, re-align, continue"
            onChange={(v) => run("/api/drive/settings", { guard: v }, `Localization guard ${v ? "on" : "off"}`)} />
        </Panel>
      )}

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
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
          <span className="mr-auto text-[12px] text-muted">
            Saved: {Object.entries(snap.map.station_counts ?? {}).map(([m, n]) => `${m} (${n})`).join(" · ") || "none yet"}
          </span>
          <button onClick={exportStations} className="flex min-h-10 items-center gap-1.5 rounded-lg border border-line-strong px-3 text-[13px] font-semibold text-muted active:bg-raised">
            <Download size={15} aria-hidden />Export</button>
          <button onClick={() => fileRef.current?.click()} className="flex min-h-10 items-center gap-1.5 rounded-lg border border-line-strong px-3 text-[13px] font-semibold text-muted active:bg-raised">
            <Upload size={15} aria-hidden />Import</button>
          <input ref={fileRef} type="file" accept="application/json,.json" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importStations(f); e.target.value = ""; }} />
        </div>
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
        <Sheet pending={pending} snap={snap} busy={busy} blocker={blocker} label={label} check={pending.kind === "goal" ? pendingCheck : undefined}
          onClose={() => setPending(null)}
          onConfirm={(areaClear) => {
            if (pending.kind === "pose") return run("/api/drive/initial_pose", pending.p, "Position set");
            if (pending.kind === "goal") return run("/api/drive/goal", { ...pending.p, area_clear: areaClear }, "Goal sent: robot is driving");
            if (pending.kind === "station") return run(`/api/drive/station/${pending.name}`, { area_clear: areaClear }, `Driving to ${label(pending.name)}`);
            if (pending.kind === "route") {
              const waypoints = draft.map((d) => d.station ? { station: d.station } : { x: d.x, y: d.y, theta: d.theta });
              return run("/api/drive/route", { waypoints, area_clear: areaClear }, `Route started: ${draft.length} stops`).then((ok) => { if (ok) setDraft([]); });
            }
            return run("/api/maps/load", { name: pending.name }, `Map ${pending.name} loaded: now set the position`);
          }} />
      )}
    </div>
  );
}

function RouteStatus({ snap, busy, onCancel }: { snap: Snapshot; busy: boolean; onCancel: () => void }) {
  const r = snap.route!.route!;
  const total = r.waypoints.length;
  const tone = r.state === "done" ? "text-ok" : r.state === "failed" ? "text-err" : r.state === "canceled" ? "text-warn" : "text-info";
  return (
    <Panel title={`Route · stop ${Math.min(r.index + 1, total)} of ${total}`} icon={<Route size={15} />}
      right={<span className={`text-[12px] font-bold uppercase tracking-wider ${tone}`}>{r.state}</span>}>
      <div className="flex flex-wrap gap-1.5">
        {r.waypoints.map((w, i) => (
          <span key={i} className={`rounded-md px-2 py-1 text-[13px] font-semibold ${
            i < r.index || r.state === "done" ? "bg-raised text-dim line-through"
              : i === r.index && snap.route!.busy ? "bg-info/15 text-info ring-1 ring-info/40" : "bg-raised text-muted"}`}>
            {i + 1}. {w.station ? (snap.map.labels[w.station] ?? w.station) : `(${w.x.toFixed(2)}, ${w.y.toFixed(2)})`}
          </span>
        ))}
      </div>
      <p className="mt-2 text-[14px] text-muted">{r.message}{r.realigns ? ` · re-aligned ${r.realigns}×` : ""}</p>
      {snap.route!.busy && (
        <Button className="mt-3 w-full" busy={busy} onClick={onCancel}>
          <X size={18} aria-hidden />Cancel route (robot stops, Nav2 stays active)
        </Button>
      )}
    </Panel>
  );
}

function Toggle({ on, label, hint, onChange }: { on: boolean; label: string; hint: string; onChange: (v: boolean) => void }) {
  return (
    <button onClick={() => onChange(!on)} role="switch" aria-checked={on}
      className={`flex min-h-16 items-center gap-3 rounded-lg border px-3 text-left ${on ? "border-ok/50 bg-ok/10" : "border-line-strong bg-raised"}`}>
      <span className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-ok" : "bg-line-strong"}`}>
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
      </span>
      <span>
        <span className="block text-[14px] font-semibold">{label}</span>
        <span className="block text-[12px] text-dim">{hint}</span>
      </span>
    </button>
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

function Sheet({ pending, snap, busy, blocker, label, check, onClose, onConfirm }: {
  pending: Pending; snap: Snapshot; busy: boolean; blocker: string; label: (s: string) => string; check?: Check;
  onClose: () => void; onConfirm: (areaClear: boolean) => void;
}) {
  const [clear, setClear] = useState(false);
  const moves = pending.kind === "goal" || pending.kind === "station" || pending.kind === "route";
  const title = pending.kind === "pose" ? "Set robot position here?"
    : pending.kind === "goal" ? "Drive the robot here?"
      : pending.kind === "station" ? `Drive to ${label(pending.name)}?`
        : pending.kind === "route" ? "Start this route?"
        : `Load map ${pending.name}?`;
  const detail = pending.kind === "pose" || pending.kind === "goal"
    ? `x ${pending.p.x.toFixed(2)} m · y ${pending.p.y.toFixed(2)} m · heading ${deg(pending.p.theta)}°`
    : pending.kind === "map" ? "The robot will need its position set again on the new map."
      : pending.kind === "route" ? "The robot drives to each stop in order and stops at each one." : "Uses the saved station pose for this map.";
  const alignNote = pending.kind === "pose" ? "The Pi will fine-tune this to fit the LiDAR to the walls (within 50 cm / 16°)." : "";
  return (
    <div className="fixed inset-x-0 bottom-[76px] z-40 border-t-2 border-line-strong bg-panel p-4 shadow-[0_-12px_40px_rgba(0,0,0,.6)]" role="dialog">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold">{title}</h3>
          <p className="num mt-0.5 text-[14px] text-muted">{detail}</p>
          {alignNote && <p className="mt-1 text-[13px] text-plan">{alignNote}</p>}
          {pending.kind === "goal" && (
            <p className={`mt-1 text-[13px] font-semibold ${!check ? "text-dim" : check.level === "bad" ? "text-err"
              : check.level === "warn" ? "text-warn" : "text-ok"}`}>
              {!check ? "Checking the point…" : check.reason}
            </p>
          )}
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
        <Button tone="primary" busy={busy} disabled={moves && (!clear || !!blocker || check?.level === "bad")} onClick={() => onConfirm(clear)}>
          {pending.kind === "pose" ? "Set position" : pending.kind === "map" ? "Load map" : pending.kind === "route" ? "Start route" : "Drive"}
        </Button>
      </div>
    </div>
  );
}

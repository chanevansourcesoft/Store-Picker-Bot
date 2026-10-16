import { useEffect, useState } from "react";
import { CheckSquare, Cpu, OctagonX, Play, RefreshCw, ShieldCheck, Square, Power } from "lucide-react";
import { api, fmtDuration } from "../api";
import type { Snapshot } from "../types";
import { Button, Dot, Panel } from "./ui";

type Toast = (t: string, tone?: "ok" | "err" | "warn") => void;

/** Start / stop the robot software on the Jetson (over SSH), AMCL recovery, link watchdog, STOP check. */
export function RobotSoftware({ snap, toast }: { snap: Snapshot; toast: Toast }) {
  const ctl = snap.jetson_ctl!;
  const st = ctl.status;
  const maps = st?.maps?.map((m) => m.name) ?? snap.ros?.maps ?? [];
  const [map, setMap] = useState<string>(st?.map || snap.robot.map_name || maps[0] || "");
  const [speed, setSpeed] = useState(st?.speed ? Number(st.speed) : 0.1);
  const [confirm, setConfirm] = useState<null | "start" | "stop" | "restart" | "bridge">(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {          // fill in the map once the Jetson status has arrived
    if (!map && (st?.map || maps.length)) setMap(st?.map || maps[0]);
  }, [st?.map, maps.length]);  // eslint-disable-line react-hooks/exhaustive-deps
  const wd = snap.ros?.watchdog;
  const routeBusy = !!snap.route?.busy;

  async function call(path: string, body: unknown, ok: string) {
    setBusy(true);
    try {
      const r = await api<{ message?: string }>(path, body);
      toast(r.message ? `${ok}: ${r.message}` : ok, "ok");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  async function doConfirmed() {
    if (confirm === "start") await call("/api/jetson/nav/start", { map, speed }, "Navigation started");
    if (confirm === "stop") await call("/api/jetson/nav/stop", {}, "Navigation stopped");
    if (confirm === "restart") {
      await call("/api/jetson/nav/stop", {}, "Navigation stopped");
      await call("/api/jetson/nav/start", { map, speed }, "Navigation started");
    }
    if (confirm === "bridge") await call("/api/jetson/bridge/restart", {}, "rosbridge restarted");
  }

  const working = busy || !!ctl.busy;
  return (
    <Panel title="Robot software (Jetson)" icon={<Cpu size={15} />}
      right={<span className="text-[12px] text-dim">{ctl.target}{ctl.age_s != null ? ` · ${ctl.age_s}s ago` : ""}</span>}>
      {ctl.error && <p className="mb-2 text-[13px] text-err">Jetson: {ctl.error}</p>}
      {ctl.busy && <p className="mb-2 text-[13px] text-info">Working: {ctl.busy}…</p>}

      <div className="grid grid-cols-3 gap-2">
        <Row label="Navigation" ok={!!st?.nav_running}
          text={st ? (st.nav_running ? `${st.map} · ${st.speed} m/s · ${fmtDuration(Number(st.nav_uptime_s))}` : "not running") : "unknown"} />
        <Row label="rosbridge" ok={!!st?.rosbridge} text={st ? (st.rosbridge ? "running" : "stopped") : "unknown"} />
        <Row label="Link watchdog" ok={!!st?.watchdog && !!wd}
          text={!st ? "unknown" : !st.watchdog ? "stopped" : wd ? (wd.armed ? "armed (Pi driving)" : "ready") : "no state"} />
      </div>

      <div className="mt-3 grid grid-cols-[1fr_auto] gap-2">
        <select value={map} onChange={(e) => setMap(e.target.value)} aria-label="Map"
          className="min-h-12 rounded-lg border border-line-strong bg-raised px-3 text-[15px] text-ink">
          {maps.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <label className="flex min-h-12 items-center gap-2 rounded-lg border border-line-strong bg-raised px-3 text-[14px] text-muted">
          speed
          <input type="number" min={0.05} max={0.3} step={0.01} value={speed} onChange={(e) => setSpeed(Number(e.target.value))}
            className="num w-20 bg-transparent text-[15px] text-ink outline-none" />
          m/s
        </label>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <Button tone="primary" busy={working && confirm === "start"} disabled={working || !!st?.nav_running || !map}
          onClick={() => setConfirm("start")}><Play size={17} aria-hidden />Start</Button>
        <Button busy={working && confirm === "restart"} disabled={working || !st?.nav_running || !map}
          onClick={() => setConfirm("restart")}><RefreshCw size={17} aria-hidden />Restart</Button>
        <Button tone="ghost" busy={working && confirm === "stop"} disabled={working || !st?.nav_running}
          onClick={() => setConfirm("stop")}><Power size={17} aria-hidden />Stop</Button>
      </div>
      <Button className="mt-2 w-full" busy={working && confirm === "bridge"} disabled={working || routeBusy}
        onClick={() => setConfirm("bridge")}><RefreshCw size={17} aria-hidden />Restart rosbridge + watchdog</Button>

      {confirm && (
        <div className="mt-3 rounded-lg border border-warn/50 bg-warn/10 p-3 text-[14px]">
          <p className="font-semibold text-[#ffd59a]">
            {confirm === "start" && `Start navigation on map ${map} at ${speed.toFixed(2)} m/s? Then set the robot position.`}
            {confirm === "stop" && "Stop navigation? The base, LiDAR filter and Nav2 shut down; any route is cancelled."}
            {confirm === "restart" && `Restart navigation on ${map} at ${speed.toFixed(2)} m/s? The robot must be localized again.`}
            {confirm === "bridge" && "Restart rosbridge and the watchdog? The dashboard reconnects in a few seconds."}
          </p>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <Button onClick={() => setConfirm(null)}>Cancel</Button>
            <Button tone="primary" busy={busy} onClick={doConfirmed}>Confirm</Button>
          </div>
        </div>
      )}

      <Toggle on={!!snap.route?.settings.amcl_recovery} disabled={working || !st?.nav_running}
        label="AMCL recovery (runtime only)"
        hint="Lets AMCL add random particles when the fit drops (alpha_fast 0.1 / slow 0.001). Not saved on the Jetson; re-applied when started from here."
        onChange={(v) => call("/api/amcl/recovery", { enabled: v }, `AMCL recovery ${v ? "on" : "off"}`)} />

      {wd && (
        <p className="mt-2 text-[12px] text-dim">
          Watchdog: cancels the Pi's goal if the Pi goes silent for {wd.timeout ?? 3} s while driving · heartbeat {wd.heartbeat_age ?? "—"} s ago · cancels so far {wd.cancels}
          {wd.last_reason ? ` · last: ${wd.last_reason}` : ""}
        </p>
      )}

      <StopCheck snap={snap} onMark={(v) => call("/api/stop/verified", { enabled: v }, v ? "STOP marked as verified" : "STOP verification removed")} />
    </Panel>
  );
}

function StopCheck({ snap, onMark }: { snap: Snapshot; onMark: (v: boolean) => void }) {
  const [seen, setSeen] = useState(false);
  const verified = snap.drive.stop_verified;
  return (
    <div className={`mt-3 rounded-lg border p-3 ${verified ? "border-ok/40 bg-ok/5" : "border-line-strong bg-raised/50"}`}>
      <div className="flex items-center gap-2 text-[14px] font-semibold">
        {verified ? <ShieldCheck size={17} className="text-ok" aria-hidden /> : <OctagonX size={17} className="text-warn" aria-hidden />}
        STOP physical check {verified ? `· verified ${typeof snap.drive.stop_verified_at === "string"
          ? snap.drive.stop_verified_at.slice(0, 16).replace("T", " ") : ""}` : "· not verified"}
      </div>
      {!verified ? (
        <>
          <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-[13px] text-muted">
            <li>Robot on a block, wheels off the ground, hand on the hardware E-stop.</li>
            <li>Map page → Send goal 1 m away. While the wheels turn, press STOP (top right).</li>
            <li>Wheels must stop at once and stay stopped; a new goal must be refused until Resume.</li>
          </ol>
          <button onClick={() => setSeen(!seen)}
            className={`mt-2 flex min-h-12 w-full items-center gap-3 rounded-lg border px-3 text-left text-[14px] font-semibold ${
              seen ? "border-ok bg-ok/10 text-ink" : "border-line-strong text-muted"}`}>
            {seen ? <CheckSquare size={20} className="text-ok" aria-hidden /> : <Square size={20} aria-hidden />}
            I did this test and the wheels stopped at once
          </button>
          <Button tone="primary" className="mt-2 w-full" disabled={!seen} onClick={() => onMark(true)}>Mark STOP as verified</Button>
        </>
      ) : (
        <Button tone="ghost" className="mt-2 w-full" onClick={() => onMark(false)}>Remove verification (test again)</Button>
      )}
    </div>
  );
}

function Row({ label, text, ok }: { label: string; text: string; ok: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-raised/50 px-3 py-2">
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-dim">{label}</div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[13px] font-semibold"><Dot color={ok ? "var(--color-ok)" : "var(--color-warn)"} />{text}</div>
    </div>
  );
}

function Toggle({ on, label, hint, disabled, onChange }: { on: boolean; label: string; hint: string; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <button onClick={() => onChange(!on)} role="switch" aria-checked={on} disabled={disabled}
      className={`mt-3 flex min-h-16 w-full items-center gap-3 rounded-lg border px-3 text-left disabled:opacity-50 ${on ? "border-ok/50 bg-ok/10" : "border-line-strong bg-raised"}`}>
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

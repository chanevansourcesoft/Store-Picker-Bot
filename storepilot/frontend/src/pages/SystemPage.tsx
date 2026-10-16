import { useEffect, useState, type ReactNode } from "react";
import { Bot, Brain, Cpu, History, Radio } from "lucide-react";
import { api, fmtDuration, fmtTime, missionDuration } from "../api";
import { Dot, MissionStatusTag, Panel } from "../components/ui";
import type { Mission, Snapshot } from "../types";

const STATE_TEXT: Record<string, [string, string]> = {
  ok: ["Running", "var(--color-ok)"],
  connected: ["Connected", "var(--color-ok)"],
  live: ["Live", "var(--color-ok)"],
  ready: ["Ready", "var(--color-ok)"],
  simulated: ["Simulated", "var(--color-warn)"],
  stub: ["Stub mode (not real)", "var(--color-warn)"],
  degraded: ["Degraded", "var(--color-warn)"],
  offline: ["Offline fallback", "var(--color-warn)"],
  stale: ["Stale", "var(--color-warn)"],
  unknown: ["Checking…", "var(--color-dim)"],
  disconnected: ["Disconnected", "var(--color-err)"],
  not_configured: ["Not configured", "var(--color-err)"],
  unavailable: ["Unavailable", "var(--color-err)"],
  error: ["Error", "var(--color-err)"],
};

function Health({ title, icon, state, rows }: { title: string; icon: ReactNode; state: string; rows: [string, ReactNode][] }) {
  const [text, color] = STATE_TEXT[state] ?? [state, "var(--color-dim)"];
  return (
    <div className="rounded-xl border border-line bg-panel p-3.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[14px] font-semibold">{icon}{title}</span>
        <span className="flex items-center gap-1.5 text-[13px] font-semibold" style={{ color }}><Dot color={color} />{text}</span>
      </div>
      <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-dim">{k}</dt>
            <dd className="num truncate text-right text-muted">{v ?? "—"}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function SystemPage({ snap }: { snap: Snapshot }) {
  const c = snap.connections;
  const [missions, setMissions] = useState<Mission[]>([]);
  const latestStatus = snap.mission ? `${snap.mission.id}:${snap.mission.status}:${snap.mission.packed}` : "";

  useEffect(() => {
    api<Mission[]>("/api/missions?limit=40").then(setMissions).catch(() => undefined);
  }, [latestStatus]);

  const finished = missions.filter((m) => !["awaiting_confirmation", "running"].includes(m.status));
  const ok = finished.filter((m) => m.status === "completed").length;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Health title="Raspberry Pi dashboard" icon={<Cpu size={16} aria-hidden />} state={c.dashboard.state} rows={[
          ["CPU temp", c.dashboard.cpu_temp_c === null ? null : `${c.dashboard.cpu_temp_c} °C`],
          ["Load (1 min)", `${c.dashboard.load_1m} / ${c.dashboard.cpus} cores`],
          ["Memory used", c.dashboard.mem_used_pct === null ? null : `${c.dashboard.mem_used_pct}%`],
          ["Disk free", `${c.dashboard.disk_free_gb} GB`],
          ["Dashboard up", fmtDuration(c.dashboard.dashboard_uptime_sec)],
        ]} />
        <Health title="Jetson link" icon={<Bot size={16} aria-hidden />} state={c.jetson.state} rows={[
          ["Endpoint", c.jetson.url ?? (c.jetson.state === "simulated" ? "built-in simulator" : "JETSON_URL not set")],
          ["Latency", c.jetson.latency_ms === null ? null : `${c.jetson.latency_ms} ms`],
          ["ROS 2 on Jetson", c.jetson.ros_available === null || c.jetson.ros_available === undefined ? null : c.jetson.ros_available ? "available" : "not available"],
          ["Last problem", c.jetson.detail],
        ]} />
        <Health title="Robot telemetry" icon={<Radio size={16} aria-hidden />} state={c.telemetry.state} rows={[
          ["Last update", c.telemetry.age_sec === null ? "never" : `${c.telemetry.age_sec}s ago`],
          ["Source", snap.mode === "mock" ? "simulator" : "Jetson /status"],
          ["Robot stopped", snap.robot.stopped === null ? null : snap.robot.stopped ? "yes" : "no"],
          ["Pick / scan / place", { simulated: "simulated", verified: "verified", unavailable: "unavailable" }[snap.safety.manipulation]],
          ["Not verified", snap.robot.unverified.length ? snap.robot.unverified.join(", ") : "—"],
        ]} />
        <Health title="AI backend" icon={<Brain size={16} aria-hidden />} state={c.ai.state} rows={[
          ["Provider", c.ai.provider ?? "none (offline)"],
          ["Parser", c.ai.parser_model ?? "keyword parser"],
          ["Planner", c.ai.planner_model],
          ["Status", c.ai.detail],
        ]} />
      </div>

      <Panel title="Mission history" icon={<History size={15} />}
        right={<span className="text-[12px] text-dim">{finished.length ? `${ok} of ${finished.length} completed · stored on this Pi` : "stored on this Pi"}</span>}
        bodyClass="py-1">
        {missions.length === 0 ? (
          <p className="py-6 text-center text-sm text-dim">No missions yet.</p>
        ) : (
          <ul>
            {missions.map((m) => (
              <li key={m.id} className="flex items-center gap-3 border-b border-line/70 py-2.5 last:border-0">
                <span className="num w-11 shrink-0 text-[14px] font-bold">M{m.id}</span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] text-ink">
                    {m.items.length ? m.items.map((i) => `${i.qty}× ${i.name}`).join(", ") : m.text}
                  </p>
                  <p className="num text-[12px] text-dim">
                    {fmtTime(m.created_at)} · {m.mode === "mock" ? "simulated" : "live"}
                    {m.total ? ` · ${m.packed}/${m.total} items` : ""}
                    {m.confirmed_at ? ` · ${fmtDuration(missionDuration(m))}` : ""}
                  </p>
                </div>
                <MissionStatusTag status={m.status} />
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

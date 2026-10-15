import { ArrowRight, BatteryMedium, Brain, Compass, Crosshair, Gauge, Hand, MapPin, Plus, Radio, TriangleAlert } from "lucide-react";
import { fmtDuration, missionDuration } from "../api";
import { EventRow } from "../components/EventRow";
import type { Tab } from "../components/Shell";
import { StoreMap } from "../components/StoreMap";
import { Button, LevelIcon, MissionStatusTag, Panel, ProgressBar, STATE_META, Stat, StateBadge, Tag } from "../components/ui";
import type { Snapshot } from "../types";

export function label(snap: Snapshot, station: string | null | undefined) {
  if (!station) return null;
  return snap.map.labels[station] ?? station;
}

export function MissionHero({ snap, goto }: { snap: Snapshot; goto: (t: Tab) => void }) {
  const r = snap.robot;
  const m = snap.mission;
  const meta = STATE_META[r.state];
  const active = m && (m.status === "running" || m.status === "awaiting_confirmation");
  const target = label(snap, r.target);

  return (
    <section className="relative overflow-hidden rounded-xl border border-line bg-panel">
      <div className="absolute inset-y-0 left-0 w-1.5" style={{ background: meta.color }} />
      <div className="p-4 pl-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-dim">Robot state</div>
            <StateBadge state={r.state} big />
            <p className="mt-2 text-[16px] text-muted">{r.detail}</p>
          </div>
          {target && (
            <div className="shrink-0 text-right">
              <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-dim">Destination</div>
              <div className="mt-1 flex items-center justify-end gap-1.5 text-xl font-bold text-info">
                <MapPin size={20} aria-hidden />{target}
              </div>
            </div>
          )}
        </div>

        <div className="mt-4 border-t border-line pt-3">
          {m ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="num text-[15px] font-bold text-ink">Mission M{m.id}</span>
                  <MissionStatusTag status={m.status} />
                </div>
                {m.status === "running" && (
                  <span className="num text-[13px] text-muted">elapsed {fmtDuration(missionDuration(m))}</span>
                )}
              </div>
              <p className="mt-1 line-clamp-2 text-[14px] italic text-muted">“{m.text}”</p>
              {m.total > 0 && (
                <div className="mt-3">
                  <div className="mb-1.5 flex items-baseline justify-between">
                    <span className="text-[15px] font-semibold">
                      <span className="num text-2xl font-bold">{m.packed}</span>
                      <span className="text-muted"> of </span>
                      <span className="num text-2xl font-bold">{m.total}</span>
                      <span className="text-muted"> items picked</span>
                    </span>
                    {m.eta?.remaining_sec != null && (
                      <span className="num text-[13px] text-muted">≈ {fmtDuration(m.eta.remaining_sec)} left · est. from {m.eta.samples} runs</span>
                    )}
                  </div>
                  <ProgressBar value={m.packed} total={m.total} color={m.status === "running" ? "var(--color-info)" : "var(--color-ok)"} />
                </div>
              )}
              {m.status === "awaiting_confirmation" && (
                <Button tone="primary" className="mt-3 w-full" onClick={() => goto("order")}>
                  Review and start mission <ArrowRight size={18} aria-hidden />
                </Button>
              )}
              {!active && (
                <Button className="mt-3 w-full" onClick={() => goto("order")}>
                  <Plus size={18} aria-hidden /> New order
                </Button>
              )}
            </>
          ) : (
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted">No missions yet.</span>
              <Button tone="primary" onClick={() => goto("order")}><Plus size={18} aria-hidden />New order</Button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export function Telemetry({ snap }: { snap: Snapshot }) {
  const r = snap.robot;
  const sim = snap.connections.telemetry.state === "simulated";
  const hintFor = (field: string) =>
    sim ? "simulated" : r.unverified.includes(field) ? "not verified yet" : undefined;
  const heading = r.pose ? ((((r.pose.theta * 180) / Math.PI) % 360) + 360) % 360 : null;
  return (
    <div className="grid grid-cols-2 gap-2">
      <Stat label="Battery" icon={<BatteryMedium size={13} />} value={r.battery === null ? null : r.battery.toFixed(0)} unit="%" hint={hintFor("battery")} />
      <Stat label="Speed" icon={<Gauge size={13} />} value={r.speed === null ? null : r.speed.toFixed(2)} unit="m/s" hint={hintFor("speed")} />
      <Stat label="Position" icon={<Crosshair size={13} />}
        value={r.pose ? `${r.pose.x.toFixed(2)}, ${r.pose.y.toFixed(2)}` : null} unit="m" hint={hintFor("pose")} />
      <Stat label="Heading" icon={<Compass size={13} />} value={heading === null ? null : heading.toFixed(0)} unit="°" hint={hintFor("pose")} />
      <Stat label="Navigation" icon={<Radio size={13} />} value={r.nav_state}
        hint={r.station ? `at ${label(snap, r.station)}` : r.nav_state === "navigating" ? "moving" : undefined} />
      <Stat label="Holding" icon={<Hand size={13} />} hint={r.unverified.includes("holding") ? "pick not available" : undefined}
        value={r.unverified.includes("holding") ? null : r.holding === null && r.nav_state !== null ? "Nothing" : r.holding} />
    </div>
  );
}

export function OpsPage({ snap, goto }: { snap: Snapshot; goto: (t: Tab) => void }) {
  const ai = snap.connections.ai;
  const missionEvents = snap.events.slice(0, 7);
  return (
    <div className="flex flex-col gap-3">
      <MissionHero snap={snap} goto={goto} />

      {snap.alerts.length > 0 && (
        <Panel title="Alerts" icon={<TriangleAlert size={15} />} bodyClass="py-2"
          right={<Tag tone={snap.alerts.some((a) => a.level === "error") ? "err" : "warn"}>{snap.alerts.length}</Tag>}>
          <ul className="flex flex-col">
            {snap.alerts.map((a, i) => (
              <li key={i} className="flex items-start gap-2.5 border-b border-line/70 py-2 text-[14px] last:border-0">
                <LevelIcon level={a.level} size={18} />
                <span className={a.level === "error" ? "text-[#ffb4b2]" : "text-ink"}>{a.text}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <div className="grid grid-cols-[1.15fr_1fr] gap-3">
        <Panel title="Live map" bodyClass="p-2">
          <button className="block w-full" onClick={() => goto("map")} aria-label="Open the full map">
            <StoreMap snap={snap} compact />
          </button>
        </Panel>
        <Panel title="Telemetry" bodyClass="p-2">
          <Telemetry snap={snap} />
        </Panel>
      </div>

      <Panel title="AI planner & mission events" icon={<Brain size={15} />}
        right={<span className="text-[12px] font-medium text-muted">{ai.planner_model ?? "—"}</span>} bodyClass="py-1">
        {snap.robot.state === "planning" && (
          <div className="flex items-center gap-2 border-b border-line/70 py-2.5 text-[15px] font-medium text-plan">
            <Brain size={17} aria-hidden /> Nemotron is deciding the next step
            <span className="thinking text-xl leading-none"><span>.</span><span>.</span><span>.</span></span>
          </div>
        )}
        {missionEvents.length ? (
          <ul>{missionEvents.map((e) => <EventRow key={e.id} e={e} dense />)}</ul>
        ) : (
          <p className="py-4 text-center text-sm text-dim">No events yet.</p>
        )}
        <button className="flex min-h-11 w-full items-center justify-center gap-1 text-[13px] font-semibold text-muted active:text-ink"
          onClick={() => goto("activity")}>
          All events <ArrowRight size={15} aria-hidden />
        </button>
      </Panel>
    </div>
  );
}

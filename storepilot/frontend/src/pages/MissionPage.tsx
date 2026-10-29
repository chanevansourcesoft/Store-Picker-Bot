import { Check, MapPin } from "lucide-react";
import { ActivityFeed } from "../components/ActivityFeed";
import { LiveDrive } from "../components/LiveDrive";
import { TableView } from "../components/TableView";
import { routeProgress, StoreMap } from "../components/StoreMap";
import { Panel, StateBadge } from "../components/ui";
import type { Snapshot } from "../types";
import { label } from "./OpsPage";

type Toast = (t: string, tone?: "ok" | "err" | "warn") => void;

function RouteStrip({ snap }: { snap: Snapshot }) {
  const r = snap.robot;
  const { stops, current } = routeProgress(snap);
  const running = snap.mission?.status === "running";
  const dest = r.target ? snap.map.stations?.[r.target] : null;
  const distance = dest && r.pose ? Math.hypot(dest.x - r.pose.x, dest.y - r.pose.y) : null;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border border-line bg-panel px-4 py-3">
      <div className="min-w-[120px]">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-dim">Destination</div>
        <div className="num text-[17px] font-semibold text-ink">
          {label(snap, r.target) ?? "—"}
          {distance !== null && <span className="ml-2 text-[13px] font-normal text-muted">{distance.toFixed(2)} m</span>}
        </div>
      </div>
      {stops.length === 0 ? (
        <p className="text-[14px] text-dim">No active route. Start a mission on the Order page.</p>
      ) : (
        <ol className="flex min-w-[280px] flex-1 items-start">
          {stops.map((s, i) => {
            const done = running && i < current;
            const now = running && i === current;
            return (
              <li key={`${s}-${i}`} className="relative flex flex-1 flex-col items-center text-center">
                {i > 0 && <span className={`absolute right-1/2 top-[15px] h-0.5 w-full ${done || now ? "bg-info" : "bg-line-strong"}`} />}
                <span className={`relative z-10 flex h-8 w-8 items-center justify-center rounded-full border-2 text-[13px] font-bold ${
                  done ? "border-info bg-info text-bg" : now ? "border-info bg-bg text-info" : "border-line-strong bg-bg text-dim"}`}>
                  {done ? <Check size={16} aria-hidden /> : now ? <MapPin size={15} aria-hidden /> : i + 1}
                </span>
                <span className={`mt-1 text-[12px] font-semibold ${now ? "text-info" : done ? "text-ink" : "text-dim"}`}>{label(snap, s)}</span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/** The mission as it happens: the whole map in one view, and what Nemotron and the robot do, side by side. */
export function MissionPage({ snap, toast }: { snap: Snapshot; toast: Toast }) {
  const live = snap.drive.available;   // real robot over rosbridge: the driving tools replace the store map
  const tables = Object.keys(snap.store ?? {}).filter((t) => t && snap.map.stations?.[t]);
  return (
    <div className="grid gap-3 lg:h-full lg:grid-cols-12 lg:gap-4">
      <div className={`flex min-w-0 flex-col gap-3 lg:col-span-8 lg:min-h-0 lg:gap-4 ${live ? "scroll-y" : ""}`}>
        {live ? <LiveDrive snap={snap} toast={toast} /> : (
          <>
            <Panel title="Store map" right={<StateBadge state={snap.robot.state} />}
              className="flex h-[min(70vh,640px)] flex-col lg:h-auto lg:min-h-0 lg:flex-1" bodyClass="flex min-h-0 flex-1 flex-col p-2">
              <div className="flex min-h-0 flex-1 gap-3">
                <div className="min-h-0 min-w-0 flex-1"><StoreMap snap={snap} fill /></div>
                {tables.map((t) => (
                  <div key={t} className="hidden w-[300px] shrink-0 rounded-lg bg-[#0d131a] p-3 md:block">
                    <TableView snap={snap} station={t} title={label(snap, t) ?? t} />
                  </div>
                ))}
              </div>
              <div className="flex shrink-0 flex-wrap gap-x-4 gap-y-1 px-2 pt-2 text-[12px] text-muted">
                <span className="flex items-center gap-1.5"><span className="h-3 w-2 rounded-sm border-2 border-[#3a4757]" />Table</span>
                <span className="flex items-center gap-1.5"><span className="h-3 w-2 rounded-sm border-2 border-brand" />Table in this order</span>
                <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm border-2 border-dashed border-ok" />Packing area</span>
                <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 bg-info" />Current leg</span>
                <span className="flex items-center gap-1.5"><span className="h-0 w-5 border-t-2 border-dashed border-info/50" />Planned route</span>
              </div>
            </Panel>
            <RouteStrip snap={snap} />
          </>
        )}
      </div>
      <ActivityFeed snap={snap} className="h-[70vh] lg:col-span-4 lg:h-auto lg:min-h-0" />
    </div>
  );
}

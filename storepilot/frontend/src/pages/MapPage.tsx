import { Check, MapPin } from "lucide-react";
import { LiveDrive } from "../components/LiveDrive";
import { routeProgress, StoreMap } from "../components/StoreMap";
import { Panel, Stat, StateBadge } from "../components/ui";
import type { Snapshot } from "../types";
import { label } from "./OpsPage";

export function MapPage({ snap, toast }: { snap: Snapshot; toast: (t: string, tone?: "ok" | "err" | "warn") => void }) {
  if (snap.drive.available) return <LiveDrive snap={snap} toast={toast} />;
  const r = snap.robot;
  const { stops, current } = routeProgress(snap);
  const running = snap.mission?.status === "running";
  const dest = r.target ? snap.map.stations?.[r.target] : null;
  const distance = dest && r.pose ? Math.hypot(dest.x - r.pose.x, dest.y - r.pose.y) : null;

  return (
    <div className="flex flex-col gap-3">
      <Panel title="Store map" right={<StateBadge state={r.state} />} bodyClass="p-2">
        <StoreMap snap={snap} />
        <div className="flex flex-wrap gap-x-4 gap-y-1 px-2 pb-1 pt-2.5 text-[12px] text-muted">
          <span className="flex items-center gap-1.5"><span className="h-3 w-2 rounded-sm border-2 border-[#3a4757]" />Shelf</span>
          <span className="flex items-center gap-1.5"><span className="h-3 w-2 rounded-sm border-2 border-warn" />Shelf in this order</span>
          <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm border-2 border-dashed border-ok" />Packing area</span>
          <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 bg-info" />Current leg</span>
          <span className="flex items-center gap-1.5"><span className="h-0 w-5 border-t-2 border-dashed border-info/50" />Planned route</span>
          {snap.map.source === "jetson" && <span className="text-dim">No occupancy grid over HTTP: stations and pose only</span>}
        </div>
      </Panel>

      <div className="grid grid-cols-4 gap-2">
        <Stat label="Destination" value={label(snap, r.target) ?? (r.pose ? "—" : null)} />
        <Stat label="Distance" value={distance !== null ? distance.toFixed(2) : r.pose ? "—" : null} unit={distance !== null ? "m" : undefined} />
        <Stat label="Nav status" value={r.nav_state} />
        <Stat label="Position" value={r.pose ? `${r.pose.x.toFixed(2)}, ${r.pose.y.toFixed(2)}` : null} />
      </div>

      <Panel title="Route progress">
        {stops.length === 0 ? (
          <p className="text-center text-sm text-dim">No active route. Start a mission to see its stops.</p>
        ) : (
          <ol className="flex items-start">
            {stops.map((s, i) => {
              const done = running && i < current;
              const now = running && i === current;
              return (
                <li key={`${s}-${i}`} className="relative flex flex-1 flex-col items-center text-center">
                  {i > 0 && <span className={`absolute right-1/2 top-[18px] h-0.5 w-full ${done || now ? "bg-info" : "bg-line-strong"}`} />}
                  <span className={`relative z-10 flex h-9 w-9 items-center justify-center rounded-full border-2 text-sm font-bold ${
                    done ? "border-info bg-info text-bg" : now ? "border-info bg-bg text-info" : "border-line-strong bg-bg text-dim"}`}>
                    {done ? <Check size={18} aria-hidden /> : now ? <MapPin size={17} aria-hidden /> : i + 1}
                  </span>
                  <span className={`mt-1.5 text-[13px] font-semibold ${now ? "text-info" : done ? "text-ink" : "text-dim"}`}>{label(snap, s)}</span>
                </li>
              );
            })}
          </ol>
        )}
      </Panel>
    </div>
  );
}

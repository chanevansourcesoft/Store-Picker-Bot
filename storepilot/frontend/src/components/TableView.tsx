import { Check } from "lucide-react";
import type { Snapshot } from "../types";

const LEVELS: [string, string][] = [["upper_shelf", "Upper shelf"], ["lower_shelf", "Lower shelf"]];

/** Front view of a table with two shelf levels (like the real one): which product is where,
 *  what this order needs, what the arm picks right now and what is already in the basket. */
export function TableView({ snap, station, title }: { snap: Snapshot; station: string; title: string }) {
  const levels = snap.store?.[station] ?? {};
  const m = snap.mission;
  const active = m && ["awaiting_confirmation", "running"].includes(m.status);
  const ordered = new Map((active ? m!.items : []).map((i) => [i.item, i.qty]));
  const picking = snap.robot.picking_item;
  const placed = new Map<string, number>();
  if (m) {
    for (const e of snap.events) {
      if (e.mission_id !== m.id || e.source !== "robot") continue;
      const hit = /^(.+?) placed in basket/.exec(e.text);
      if (hit) placed.set(hit[1], (placed.get(hit[1]) ?? 0) + 1);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-muted">{title} · front view</span>
        {picking && <span className="text-[12px] font-semibold text-pick">Picking…</span>}
      </div>
      {/* table top */}
      <div className="h-3 rounded-t-md bg-[#7a4a26]" />
      <div className="flex flex-1 border-x-[6px] border-b-[6px] border-[#6a3f20]">
        {/* open side: two shelf levels */}
        <div className="flex flex-[3] flex-col bg-[#1a1410]">
          {LEVELS.map(([id, name], i) => (
            <div key={id} className={`flex flex-1 flex-col justify-end px-2 pb-1.5 pt-2 ${i === 0 ? "border-b-[5px] border-[#6a3f20]" : ""}`}>
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-[#b08b6a]">{name}</div>
              <div className="flex flex-wrap gap-1.5">
                {(levels[id] ?? []).map((p) => {
                  const need = ordered.get(p.id);
                  const done = placed.get(p.name) ?? 0;
                  const now = picking === p.id;
                  return (
                    <span key={p.id} title={`${p.name}: ${p.stock} in stock`}
                      className={`flex items-center gap-1 rounded-md border px-1.5 py-1 text-[12px] font-semibold leading-none ${
                        now ? "pulse-dot border-pick bg-pick/20 text-pick"
                          : need ? "border-brand bg-brand/15 text-ink" : "border-[#4a3a2c] bg-[#241c15] text-[#cdb8a3]"}`}>
                      {p.name}
                      {need ? <span className="num text-[11px] text-brand">×{need}</span> : null}
                      {done > 0 && <span className="flex items-center text-ok"><Check size={12} aria-hidden />{done}</span>}
                    </span>
                  );
                })}
                {!(levels[id] ?? []).length && <span className="text-[11px] text-dim">empty</span>}
              </div>
            </div>
          ))}
        </div>
        {/* closed cabinet door */}
        <div className="relative flex-[2] border-l-[5px] border-[#6a3f20] bg-[#5a361c]">
          <span className="absolute left-1/2 top-4 h-1 w-8 -translate-x-1/2 rounded-full bg-[#c9c3bb]" />
        </div>
      </div>
      <div className="mx-[6px] flex justify-between">
        <span className="h-2 w-2 bg-[#6a3f20]" /><span className="h-2 w-2 bg-[#6a3f20]" />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
        <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm border border-brand bg-brand/20" />In this order</span>
        <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm border border-pick bg-pick/20" />Picking now</span>
        <span className="flex items-center gap-1 text-ok"><Check size={11} aria-hidden />In basket</span>
      </div>
    </div>
  );
}

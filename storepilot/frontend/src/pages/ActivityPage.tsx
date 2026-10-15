import { useEffect, useState } from "react";
import { api } from "../api";
import { EventRow } from "../components/EventRow";
import { Panel } from "../components/ui";
import type { EventItem, Snapshot } from "../types";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "ai", label: "AI planner" },
  { id: "robot", label: "Robot" },
  { id: "operator", label: "Operator" },
  { id: "system", label: "System" },
] as const;

export function ActivityPage({ snap }: { snap: Snapshot }) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");
  const [older, setOlder] = useState<EventItem[] | null>(null);

  // the snapshot carries the newest 60; "Load more" fetches up to 300
  useEffect(() => { setOlder(null); }, [filter]);
  const events = older ? mergeEvents(snap.events, older) : snap.events;
  const shown = events.filter((e) => filter === "all" || e.source === filter);

  return (
    <Panel title="Mission events" bodyClass="pt-2"
      right={<span className="text-[12px] text-dim">{snap.mode === "mock" ? "Simulated run" : "From the robot and planner"}</span>}>
      <div className="mb-1 flex gap-2 overflow-x-auto pb-2">
        {FILTERS.map((f) => (
          <button key={f.id} onClick={() => setFilter(f.id)}
            className={`min-h-10 shrink-0 rounded-full border px-4 text-[14px] font-semibold ${
              filter === f.id ? "border-info bg-info/15 text-info" : "border-line text-muted active:bg-raised"}`}>
            {f.label}
          </button>
        ))}
      </div>
      {shown.length ? (
        <ul>{shown.map((e) => <EventRow key={e.id} e={e} />)}</ul>
      ) : (
        <p className="py-6 text-center text-sm text-dim">No events of this kind yet.</p>
      )}
      {!older && events.length >= 60 && (
        <button className="mt-1 min-h-12 w-full rounded-lg border border-line text-[14px] font-semibold text-muted active:bg-raised"
          onClick={() => api<EventItem[]>("/api/events?limit=300").then(setOlder).catch(() => undefined)}>
          Load older events
        </button>
      )}
    </Panel>
  );
}

function mergeEvents(live: EventItem[], older: EventItem[]) {
  const seen = new Set(live.map((e) => e.id));
  return [...live, ...older.filter((e) => !seen.has(e.id))];
}

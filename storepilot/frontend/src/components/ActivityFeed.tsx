import { useEffect, useMemo, useState } from "react";
import { Brain } from "lucide-react";
import { api, fmtTime, niceModels } from "../api";
import type { EventItem, Snapshot } from "../types";
import { LevelIcon, Panel, SOURCE_META } from "./ui";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "ai", label: "Nemotron" },
  { id: "robot", label: "Robot" },
] as const;
type Filter = (typeof FILTERS)[number]["id"];

/** Which AI did this, from the event text the backend writes (missions.py). */
function aiBadge(text: string): { label: string; tone: string } | null {
  if (/blocked by safety check/.test(text)) return { label: "Safety check", tone: "border-err/50 text-err" };
  if (/^Autopilot decided|offline autopilot/.test(text)) return { label: "Autopilot", tone: "border-line-strong text-muted" };
  if (/keyword parser|rule-based/.test(text)) return { label: "Offline", tone: "border-line-strong text-muted" };
  if (/Ultra/.test(text)) return { label: "Nemotron Ultra", tone: "border-plan/60 text-plan" };
  if (/Super| decided: |^Planner started/.test(text)) return { label: "Nemotron Super", tone: "border-plan/60 text-plan" };
  if (/Nano|^Order parsed|^Not named in the order|^Not stocked/.test(text)) return { label: "Nemotron Nano", tone: "border-plan/60 text-plan" };
  return null;
}

function robotBadge(text: string) {
  return /GR00T/.test(text) ? { label: "GR00T", tone: "border-pick/60 text-pick" } : null;
}

/** "Nemotron Super decided: drive to Shelf A" -> "drive to Shelf A";
 *  "Mission report by NVIDIA Nemotron (…): text" -> "Mission report: text". */
function shortText(e: EventItem) {
  if (/ decided: /.test(e.text)) return e.text.split(" decided: ")[1].replace(/^./, (c) => c.toUpperCase());
  if (e.text.startsWith("Mission report by ")) return "Mission report: " + e.text.slice(e.text.indexOf(": ") + 2);
  if (e.text.startsWith("Order parsed by ")) return "Order understood: " + e.text.slice(e.text.lastIndexOf(": ") + 2);
  return niceModels(e.text);
}

interface Group { head: EventItem; children: EventItem[] }

/** A planner decision followed by what the robot answered (and any automatic retries). */
function groupDecisions(newestFirst: EventItem[]): Group[] {
  const groups: Group[] = [];
  let open: Group | null = null;
  for (const e of [...newestFirst].reverse()) {
    if (e.source === "ai" && / decided: /.test(e.text)) {
      open = { head: e, children: [] };
      groups.push(open);
    } else if (e.source === "robot" && open && e.mission_id === open.head.mission_id) {
      open.children.push(e);
    } else {
      groups.push({ head: e, children: [] });
      if (e.source !== "robot") open = null;
    }
  }
  return groups.reverse();
}

function Row({ e, child = false }: { e: EventItem; child?: boolean }) {
  const badge = e.source === "ai" ? aiBadge(e.text) : e.source === "robot" ? robotBadge(e.text) : null;
  const Src = SOURCE_META[e.source]?.icon;
  return (
    <div className={`flex items-start gap-2.5 ${child ? "py-1.5" : "py-2"}`}>
      <span className="num w-[54px] shrink-0 pt-0.5 text-[11px] text-dim">{fmtTime(e.ts)}</span>
      <LevelIcon level={e.level} size={child ? 14 : 16} />
      <div className="min-w-0 flex-1">
        <p className={`leading-snug ${child ? "text-[13px] text-muted" : "text-[14px] text-ink"}`}>{shortText(e)}</p>
        {!child && (
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-dim">
            {badge ? (
              <span className={`rounded border px-1.5 py-px ${badge.tone}`}>{badge.label}</span>
            ) : (
              <span className="flex items-center gap-1">{Src && <Src size={11} aria-hidden />}{SOURCE_META[e.source]?.label ?? e.source}</span>
            )}
            {e.mission_id !== null && <span className="num normal-case">M{e.mission_id}</span>}
          </div>
        )}
      </div>
      {child && badge && <span className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider ${badge.tone}`}>{badge.label}</span>}
    </div>
  );
}

export function ActivityFeed({ snap, className = "" }: { snap: Snapshot; className?: string }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [older, setOlder] = useState<EventItem[] | null>(null);
  useEffect(() => { setOlder(null); }, [filter]);

  const events = useMemo(() => {
    if (!older) return snap.events;
    const seen = new Set(snap.events.map((e) => e.id));
    return [...snap.events, ...older.filter((e) => !seen.has(e.id))];
  }, [snap.events, older]);

  const groups = useMemo(() => filter === "all" ? groupDecisions(events)
    : events.filter((e) => e.source === filter).map((e) => ({ head: e, children: [] })), [events, filter]);

  return (
    <Panel title="Mission activity" className={`flex flex-col ${className}`} bodyClass="flex min-h-0 flex-1 flex-col p-0">
      <div className="flex shrink-0 gap-1.5 overflow-x-auto border-b border-line px-3 py-2">
        {FILTERS.map((f) => (
          <button key={f.id} onClick={() => setFilter(f.id)}
            className={`min-h-8 shrink-0 rounded-full border px-3 text-[13px] font-semibold ${
              filter === f.id ? "border-brand bg-brand/15 text-brand" : "border-line text-muted hover:text-ink"}`}>
            {f.label}
          </button>
        ))}
      </div>
      {snap.robot.state === "planning" && (
        <div className="flex shrink-0 items-center gap-2 border-b border-line bg-plan/10 px-4 py-2 text-[13px] font-medium text-plan">
          <Brain size={15} aria-hidden /> Nemotron Super is deciding the next step
          <span className="thinking leading-none"><span>.</span><span>.</span><span>.</span></span>
        </div>
      )}
      <ul className="scroll-y min-h-0 flex-1 px-3">
        {groups.length ? groups.map((g) => (
          <li key={g.head.id} className="border-b border-line/70 last:border-0">
            <Row e={g.head} />
            {g.children.length > 0 && (
              <div className="mb-2 ml-[64px] border-l-2 border-line-strong pl-2">
                {g.children.map((c) => <Row key={c.id} e={c} child />)}
              </div>
            )}
          </li>
        )) : <li className="py-6 text-center text-sm text-dim">No events of this kind yet.</li>}
        {!older && events.length >= 60 && (
          <li className="py-2">
            <button className="min-h-10 w-full rounded-lg border border-line text-[13px] font-semibold text-muted hover:text-ink"
              onClick={() => api<EventItem[]>("/api/events?limit=300").then(setOlder).catch(() => undefined)}>
              Load older events
            </button>
          </li>
        )}
      </ul>
    </Panel>
  );
}

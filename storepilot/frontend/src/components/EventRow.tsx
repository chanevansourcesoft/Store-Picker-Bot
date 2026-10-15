import { fmtTime } from "../api";
import type { EventItem } from "../types";
import { LevelIcon, SOURCE_META } from "./ui";

export function EventRow({ e, dense = false }: { e: EventItem; dense?: boolean }) {
  const Src = SOURCE_META[e.source]?.icon;
  return (
    <li className={`flex items-start gap-3 border-b border-line/70 last:border-0 ${dense ? "py-2" : "py-2.5"}`}>
      <span className="num w-[62px] shrink-0 pt-0.5 text-[12px] text-dim">{fmtTime(e.ts)}</span>
      <LevelIcon level={e.level} size={17} />
      <div className="min-w-0 flex-1">
        <p className={`${dense ? "text-[14px]" : "text-[15px]"} leading-snug text-ink`}>{e.text}</p>
        <div className="mt-0.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-dim">
          {Src && <Src size={12} aria-hidden />}
          {SOURCE_META[e.source]?.label ?? e.source}
          {e.mission_id !== null && <span className="num normal-case">· M{e.mission_id}</span>}
          {e.simulated && <span className="rounded bg-warn/10 px-1 text-warn">sim</span>}
        </div>
      </div>
    </li>
  );
}

import { useEffect, useState } from "react";
import { BarChart3, Brain, FileText } from "lucide-react";
import { api, fmtDuration, fmtTime, missionDuration, niceModels } from "../api";
import type { Mission, Snapshot } from "../types";
import { Button, Panel, Stat } from "./ui";

type Toast = (t: string, tone?: "ok" | "err" | "warn") => void;

function today(iso: string) {
  const d = new Date(iso);
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}

/** Today's totals, and the owner's shift report written by Nemotron Ultra on request. */
export function SessionStats({ snap, toast }: { snap: Snapshot; toast: Toast }) {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [busy, setBusy] = useState(false);
  const m = snap.mission;
  const key = m ? `${m.id}:${m.status}` : "";
  useEffect(() => {
    api<Mission[]>("/api/missions?limit=200").then(setMissions).catch(() => undefined);
  }, [key]);

  const ran = missions.filter((x) => x.confirmed_at && x.finished_at && today(x.created_at));
  const completed = ran.filter((x) => x.status === "completed").length;
  const delivered = ran.reduce((n, x) => n + x.packed, 0);
  const ordered = ran.reduce((n, x) => n + x.total, 0);
  const times = ran.filter((x) => x.status.startsWith("completed")).map((x) => missionDuration(x) ?? 0);
  const avg = times.length ? times.reduce((a, b) => a + b, 0) / times.length : null;
  const report = snap.shift_report;
  const ai = snap.connections.ai;

  async function write() {
    setBusy(true);
    try {
      await api("/api/shift-report", {});
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Today's shift" icon={<BarChart3 size={15} />} bodyClass="p-2">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Missions completed" value={ran.length ? `${completed} of ${ran.length}` : "—"} />
        <Stat label="Items delivered" value={ran.length ? delivered : "—"} />
        <Stat label="Avg mission time" value={avg === null ? "—" : fmtDuration(avg)} />
        <Stat label="Delivered vs ordered" value={ordered ? `${Math.round((100 * delivered) / ordered)}` : "—"} unit={ordered ? "%" : undefined} />
      </div>

      <div className="mt-2 rounded-lg border border-plan/40 bg-plan/5 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-[13px] font-semibold text-plan">
            <FileText size={14} aria-hidden />Shift report for the owner
          </span>
          {report && <span className="num text-[11px] text-dim">{fmtTime(report.at)}</span>}
        </div>
        {report ? (
          <>
            <p className="mt-2 text-[14px] leading-relaxed text-ink">{report.text}</p>
            <p className="mt-1.5 flex items-center gap-1 text-[11px] text-dim"><Brain size={11} aria-hidden />{niceModels(report.by)}</p>
          </>
        ) : (
          <p className="mt-2 text-[13px] text-muted">
            {ai.summary_model ? "Nemotron Ultra" : "A rule-based summary"} reads today's missions, pick results and stock,
            and tells the owner how the shift went, what to restock and what to improve.
          </p>
        )}
        <Button className="mt-2.5 w-full" busy={busy} onClick={write}>
          <Brain size={16} aria-hidden />{report ? "Update shift report" : "Write shift report"}
        </Button>
      </div>
    </Panel>
  );
}

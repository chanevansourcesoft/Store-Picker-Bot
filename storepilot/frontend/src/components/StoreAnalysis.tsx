import { useEffect, useState, type ReactNode } from "react";
import { BarChart3, Brain, Lock, PackagePlus, PackageSearch } from "lucide-react";
import { api, fmtDuration, fmtTime, niceModels, useTeamPassword } from "../api";
import type { Analysis, Snapshot } from "../types";
import { Button, Panel, Stat } from "./ui";

type Toast = (t: string, tone?: "ok" | "err" | "warn") => void;

/** Today's numbers, refreshed when a mission changes state or stock changes. */
export function useAnalysis(snap: Snapshot) {
  const [data, setData] = useState<Analysis | null>(null);
  const m = snap.mission;
  const stock = Object.values(snap.store ?? {}).flatMap((l) => Object.values(l).flat()).map((p) => p.stock).join(",");
  const key = `${m?.id}:${m?.status}:${stock}`;
  useEffect(() => {
    api<Analysis>("/api/analysis").then(setData).catch(() => undefined);
  }, [key]);
  return data;
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v}`);

function Row({ label, value, tone }: { label: string; value: ReactNode; tone?: "ok" | "warn" | "err" }) {
  const color = tone === "err" ? "text-err" : tone === "warn" ? "text-warn" : tone === "ok" ? "text-ok" : "text-ink";
  return (
    <tr className="border-t border-line/70">
      <td className="py-1.5 pr-3 text-[13px] text-muted">{label}</td>
      <td className={`num py-1.5 text-right text-[14px] font-semibold ${color}`}>{value}</td>
    </tr>
  );
}

function SectionRow({ children }: { children: ReactNode }) {
  return (
    <tr><td colSpan={2} className="pb-1 pt-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-dim">{children}</td></tr>
  );
}

/** Right column of Operations: today's key numbers and the order / robot breakdown. */
export function TodayPanel({ a }: { a: Analysis | null }) {
  const o = a?.orders;
  const r = a?.robot;
  return (
    <Panel title="Today" icon={<BarChart3 size={15} />} bodyClass="p-2">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Order success" value={o?.started ? pct(o.success_rate) : "—"} unit={o?.started ? "%" : undefined} />
        <Stat label="Items delivered" value={o?.started ? `${o.items_delivered} of ${o.items_ordered}` : "—"} />
        <Stat label="Arm pick success" value={r?.pick_attempts ? pct(r.pick_success_rate) : "—"} unit={r?.pick_attempts ? "%" : undefined} />
        <Stat label="Avg mission time" value={r?.average_mission_seconds != null ? fmtDuration(r.average_mission_seconds) : "—"} />
      </div>
      {a && (
        <table className="mt-1 w-full px-2">
          <tbody>
            <SectionRow>Orders</SectionRow>
            <Row label="Placed today" value={o!.placed} />
            <Row label="Completed" value={o!.completed} tone={o!.completed ? "ok" : undefined} />
            <Row label="Completed with issues" value={o!.completed_with_issues} tone={o!.completed_with_issues ? "warn" : undefined} />
            <Row label="Failed or stopped" value={o!.failed + o!.stopped} tone={o!.failed + o!.stopped ? "err" : undefined} />
            <Row label="Refused before start" value={o!.refused_for_stock ? `${o!.refused} (${o!.refused_for_stock} out of stock)` : o!.refused}
              tone={o!.refused ? "warn" : undefined} />
            <SectionRow>Robot</SectionRow>
            <Row label="Arm attempts (missed grasps)" value={`${r!.pick_attempts} (${r!.missed_grasps})`} tone={r!.missed_grasps ? "warn" : undefined} />
            <Row label="Failed drives" value={r!.failed_drives} tone={r!.failed_drives ? "warn" : undefined} />
            <Row label="Commands blocked by safety checks" value={r!.safety_blocks} tone={r!.safety_blocks ? "warn" : undefined} />
          </tbody>
        </table>
      )}
    </Panel>
  );
}

const STATUS = {
  ok: ["OK", "border-ok/40 bg-ok/10 text-ok"],
  low: ["Low", "border-warn/50 bg-warn/10 text-warn"],
  out: ["Out of stock", "border-err/50 bg-err/10 text-err"],
} as const;

const th = "px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-[0.08em] text-dim";

/** Full width on Operations: stock and restock (exact numbers) and Nemotron Ultra's findings and actions. */
export function StoreAnalysisPanel({ snap, a, toast }: { snap: Snapshot; a: Analysis | null; toast: Toast }) {
  const [busy, setBusy] = useState(false);
  const [restocking, setRestocking] = useState<string | null>(null);   // product id, "all", or null
  const [password, setPassword] = useTeamPassword();
  const team = snap.media.upload_enabled;
  const report = snap.shift_report;
  const ultra = !!snap.connections.ai.summary_model;
  const toRestock = a?.products.filter((p) => p.restock > 0) ?? [];

  async function restock(which: { id: string; name: string; qty: number } | "all") {
    if (!password) return toast("Enter the team password first.", "warn");
    setRestocking(which === "all" ? "all" : which.id);
    try {
      await api("/api/stock/restock", which === "all" ? { password, all_low: true }
        : { password, items: [{ item: which.id, qty: which.qty }] });
      toast(which === "all" ? "All low products restocked" : `Restocked ${which.qty} ${which.name}`, "ok");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setRestocking(null);
    }
  }

  async function analyse() {
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
    <Panel title="Store analysis" icon={<PackageSearch size={15} />} bodyClass="p-0"
      right={<span className="text-[12px] text-muted">
        {toRestock.length ? `${toRestock.length} product${toRestock.length > 1 ? "s" : ""} to restock` : "Stock OK"}</span>}>
      {/* team: restock (needs the team password) */}
      {team ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-raised/30 px-3 py-2">
          <span className="flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-[0.08em] text-muted">
            <Lock size={13} aria-hidden />Team
          </span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder="Team password" autoComplete="current-password" aria-label="Team password"
            className="h-9 w-48 rounded-md border border-line-strong bg-raised px-2.5 text-[14px] text-ink placeholder:text-dim focus:border-info focus:outline-none" />
          <Button className="ml-auto min-h-9 px-3 text-[13px]" busy={restocking === "all"}
            disabled={!toRestock.length || !password || !!restocking} onClick={() => restock("all")}>
            <PackagePlus size={15} aria-hidden />Restock all low items{toRestock.length ? ` (${toRestock.length})` : ""}
          </Button>
        </div>
      ) : (
        <p className="border-b border-line px-3 py-2 text-[12px] text-dim">
          Restocking is switched off on this server: set <span className="num text-muted">STOREPILOT_UPLOAD_PASSWORD</span> (the team password).
        </p>
      )}

      {/* stock and restock */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px]">
          <thead className="bg-raised/50">
            <tr>
              <th className={th}>Product</th><th className={th}>Where</th>
              <th className={`${th} text-right`}>In stock</th><th className={`${th} text-right`}>Ordered today</th>
              <th className={`${th} text-right`}>Delivered today</th><th className={th}>Status</th>
              <th className={`${th} text-right`}>Restock</th>
            </tr>
          </thead>
          <tbody>
            {(a?.products ?? []).map((p) => {
              const [text, cls] = STATUS[p.status];
              return (
                <tr key={p.id} className="border-t border-line/70">
                  <td className="px-3 py-2 text-[14px] font-semibold text-ink">{p.name}</td>
                  <td className="px-3 py-2 text-[13px] text-muted">
                    {p.shelf}{p.level ? ` · ${p.level === "upper_shelf" ? "upper" : p.level === "lower_shelf" ? "lower" : p.level} shelf` : ""}
                  </td>
                  <td className="num px-3 py-2 text-right text-[14px] text-ink">{p.stock}<span className="text-dim"> / {p.start}</span></td>
                  <td className="num px-3 py-2 text-right text-[14px] text-ink">{p.ordered_today}</td>
                  <td className="num px-3 py-2 text-right text-[14px] text-ink">{p.delivered_today}</td>
                  <td className="px-3 py-2"><span className={`rounded-md border px-2 py-0.5 text-[12px] font-semibold ${cls}`}>{text}</span></td>
                  <td className="px-3 py-2 text-right">
                    {p.restock && team ? (
                      <button onClick={() => restock({ id: p.id, name: p.name, qty: p.restock })}
                        disabled={!password || !!restocking} title={password ? `Add ${p.restock} ${p.name}` : "Enter the team password first"}
                        className="num inline-flex items-center gap-1 rounded-md border border-warn/60 bg-warn/10 px-2 py-1 text-[13px] font-bold text-warn hover:bg-warn/20 disabled:cursor-not-allowed disabled:opacity-40">
                        <PackagePlus size={13} aria-hidden />{restocking === p.id ? "…" : `Restock +${p.restock}`}
                      </button>
                    ) : (
                      <span className={`num text-[14px] font-bold ${p.restock ? "text-warn" : "text-dim"}`}>{p.restock ? `+${p.restock}` : "—"}</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {!a && <tr><td colSpan={7} className="px-3 py-4 text-center text-sm text-dim">Loading…</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="border-t border-line/70 px-3 py-2 text-[12px] text-dim">
        Low = 30 % of the starting stock or less (at least 3). Restock brings a product back to its starting stock; every restock is logged.
      </p>

      {/* Nemotron Ultra's analysis */}
      <div className="border-t border-line bg-plan/5 px-3 pb-3 pt-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-[13px] font-semibold uppercase tracking-[0.08em] text-plan">
            <Brain size={15} aria-hidden />{ultra ? "Nemotron Ultra analysis" : "Analysis"}
          </span>
          <div className="flex items-center gap-3">
            {report && <span className="text-[12px] text-dim">{niceModels(report.by)} · {fmtTime(report.at)}</span>}
            <Button busy={busy} onClick={analyse} className="min-h-10 px-3 text-[14px]">
              <Brain size={16} aria-hidden />{report ? "Update analysis" : "Analyse today"}
            </Button>
          </div>
        </div>
        {report?.insights?.length ? (
          <>
            <p className="mt-3 text-[15px] font-semibold text-ink">{report.text}</p>
            <div className="mt-2 overflow-x-auto rounded-lg border border-line">
              <table className="w-full min-w-[640px]">
                <thead className="bg-raised/50">
                  <tr><th className={`${th} w-[110px]`}>Area</th><th className={th}>What the numbers show</th><th className={th}>Recommended action</th></tr>
                </thead>
                <tbody>
                  {report.insights.map((i, n) => (
                    <tr key={n} className="border-t border-line/70 align-top">
                      <td className="px-3 py-2"><span className="rounded-md border border-plan/50 px-2 py-0.5 text-[12px] font-semibold text-plan">{i.area}</span></td>
                      <td className="px-3 py-2 text-[14px] text-ink">{i.finding}</td>
                      <td className="px-3 py-2 text-[14px] font-medium text-ink">{i.action}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="mt-2 text-[14px] text-muted">
            {ultra ? "Nemotron Ultra" : "The rule-based analysis"} reads today's orders, robot results and stock, and
            lists what it found and what the owner should do: what to restock, what went wrong, what to improve.
          </p>
        )}
      </div>
    </Panel>
  );
}

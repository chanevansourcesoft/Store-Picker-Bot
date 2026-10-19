import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Brain, CheckSquare, Minus, Play, Plus, Send, Square, Trash2, X } from "lucide-react";
import { api, fmtDuration } from "../api";
import { Button, MissionStatusTag, Panel, Tag } from "../components/ui";
import type { CatalogItem, Mission, Snapshot } from "../types";
import { label } from "./OpsPage";

const EXAMPLES = [
  "2 chips packets and 1 Frooti to the packing area",
  "Pick 3 classic chips",
  "Pick 2 packets of Maggi and 1 Coke bottle",
];

function ConfirmDialog({ mission, snap, onCancel, onStart, busy }: {
  mission: Mission; snap: Snapshot; onCancel: () => void; onStart: () => void; busy: boolean;
}) {
  const [clear, setClear] = useState(false);
  const stops = [...mission.route, "customer", "home"].map((s) => label(snap, s)).join(" → ");
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-5" role="dialog" aria-modal="true">
      <div className="w-full max-w-[640px] rounded-2xl border-2 border-warn/60 bg-panel p-6 shadow-2xl">
        <div className="flex items-center gap-3 text-warn">
          <AlertTriangle size={28} aria-hidden />
          <h2 className="text-xl font-bold">Start a real robot mission?</h2>
        </div>
        <p className="mt-3 text-[15px] text-muted">
          The physical robot will drive <span className="font-semibold text-ink">{stops}</span> and use its arm to pick{" "}
          <span className="font-semibold text-ink">{mission.total} item{mission.total === 1 ? "" : "s"}</span>.
        </p>
        <button onClick={() => setClear(!clear)}
          className={`mt-5 flex min-h-16 w-full items-center gap-3 rounded-xl border-2 px-4 text-left text-[16px] font-semibold ${
            clear ? "border-ok bg-ok/10 text-ink" : "border-line-strong bg-raised text-muted"}`}>
          {clear ? <CheckSquare size={28} className="shrink-0 text-ok" aria-hidden /> : <Square size={28} className="shrink-0" aria-hidden />}
          The robot's path is clear and nobody is in the work area
        </button>
        <p className="mt-3 text-[13px] text-dim">Keep the hardware emergency stop within reach.</p>
        <div className="mt-5 grid grid-cols-2 gap-3">
          <Button onClick={onCancel}><X size={18} aria-hidden />Cancel</Button>
          <Button tone="primary" disabled={!clear} busy={busy} onClick={onStart}><Play size={18} aria-hidden />Start mission</Button>
        </div>
      </div>
    </div>
  );
}

function Review({ m, snap, toast }: { m: Mission; snap: Snapshot; toast: (t: string, tone?: "ok" | "err" | "warn") => void }) {
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState(false);
  const waiting = m.status === "awaiting_confirmation";

  async function start() {
    setBusy(true);
    try {
      await api(`/api/missions/${m.id}/confirm`, { area_clear: snap.safety.needs_area_confirmation });
      toast(`Mission M${m.id} started`, "ok");
      setDialog(false);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }
  async function discard() {
    try {
      await api(`/api/missions/${m.id}/discard`, {});
      toast(`Mission M${m.id} discarded`, "warn");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }

  const inferred = m.items.filter((i) => i.inferred);
  return (
    <Panel title={`Mission M${m.id} · review`} right={<MissionStatusTag status={m.status} />}>
      <p className="text-[15px] italic text-muted">“{m.text}”</p>

      <table className="mt-3 w-full text-[15px]">
        <thead>
          <tr className="text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-dim">
            <th className="pb-2 font-semibold">Item</th><th className="pb-2 font-semibold">Shelf</th>
            <th className="pb-2 text-right font-semibold">Qty</th>
          </tr>
        </thead>
        <tbody>
          {m.items.map((i) => (
            <tr key={i.item} className="border-t border-line/70">
              <td className="py-2.5 font-medium">
                {i.name}
                {i.inferred && <span className="ml-2 align-middle"><Tag tone="warn">check: inferred</Tag></span>}
              </td>
              <td className="py-2.5 text-muted">{label(snap, i.shelf) ?? "—"}</td>
              <td className="num py-2.5 text-right text-lg font-bold">{i.qty}</td>
            </tr>
          ))}
          {m.items.length === 0 && (
            <tr><td colSpan={3} className="py-3 text-dim">No products from this store recognised.</td></tr>
          )}
        </tbody>
      </table>

      {m.unknown.length > 0 && (
        <div className="mt-3 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[14px] text-[#ffd59a]">
          Not stocked here (will not be picked): <span className="font-semibold">{m.unknown.join(", ")}</span>
        </div>
      )}
      {inferred.length > 0 && (
        <div className="mt-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[14px] text-[#ffd59a]">
          The AI added {inferred.map((i) => i.name).join(", ")} although the order does not name it. Discard if that is wrong.
        </div>
      )}
      {m.problems.length > 0 && m.status === "rejected" && (
        <div className="mt-2 rounded-lg border border-err/50 bg-err/10 px-3 py-2 text-[14px] text-[#ffb4b2]">
          {m.problems.join(" ")}
        </div>
      )}

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[14px]">
        <dt className="text-dim">Route</dt>
        <dd className="font-medium">{[...m.route, "customer", "home"].map((s) => label(snap, s)).join(" → ")}</dd>
        <dt className="text-dim">Parsed by</dt>
        <dd className="flex items-center gap-1.5 text-muted"><Brain size={14} aria-hidden />{m.parser ?? "—"}</dd>
        <dt className="text-dim">Estimate</dt>
        <dd className="text-muted">{m.eta ? `≈ ${fmtDuration(m.eta.total_sec)} (from ${m.eta.samples} past missions)` : "Not enough history for a reliable estimate"}</dd>
      </dl>

      {waiting && (
        <>
          {!snap.safety.can_start && (
            <p className="mt-3 flex items-center gap-2 text-[14px] font-medium text-warn">
              <AlertTriangle size={16} aria-hidden />Cannot start: {snap.safety.cannot_start_reason}
            </p>
          )}
          <div className="mt-4 grid grid-cols-[1fr_2fr] gap-3">
            <Button tone="ghost" onClick={discard}><Trash2 size={18} aria-hidden />Discard</Button>
            <Button tone="primary" busy={busy} disabled={!snap.safety.can_start}
              onClick={() => (snap.safety.needs_area_confirmation ? setDialog(true) : start())}>
              <Play size={18} aria-hidden />{snap.mode === "mock" ? "Start simulated mission" : "Start mission…"}
            </Button>
          </div>
        </>
      )}
      {dialog && <ConfirmDialog mission={m} snap={snap} busy={busy} onCancel={() => setDialog(false)} onStart={start} />}
    </Panel>
  );
}

let catalogCache: CatalogItem[] = [];  // keeps the product cards instant when the page is reopened

export function OrderPage({ snap, toast }: { snap: Snapshot; toast: (t: string, tone?: "ok" | "err" | "warn") => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<CatalogItem[]>(catalogCache);
  const [qty, setQty] = useState<Record<string, number>>({});
  const m = snap.mission;
  const running = m?.status === "running";

  useEffect(() => {
    api<CatalogItem[]>("/api/catalog").then((c) => { catalogCache = c; setCatalog(c); }).catch(() => undefined);
  }, [m?.status]);

  const built = useMemo(() => {
    const parts = catalog.filter((c) => qty[c.id]).map((c) => `${qty[c.id]} ${c.name}`);
    if (!parts.length) return "";
    const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0];
    return `Pick ${list} and bring them to the packing area`;
  }, [qty, catalog]);

  useEffect(() => { if (built) setText(built); }, [built]);

  async function submit() {
    setBusy(true);
    try {
      const mission = await api<Mission>("/api/orders", { text });
      toast(mission.status === "rejected" ? `Order checked: cannot be done (M${mission.id})` : `Order parsed: mission M${mission.id} ready for review`,
        mission.status === "rejected" ? "err" : "ok");
      setQty({});
      setText("");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const showReview = m && ["awaiting_confirmation", "rejected"].includes(m.status);

  return (
    <div className="flex flex-col gap-3">
      {showReview && <Review m={m} snap={snap} toast={toast} />}
      {running && (
        <Panel>
          <p className="text-[15px]"><span className="font-semibold">Mission M{m.id} is running</span>
            <span className="text-muted"> — {m.packed} of {m.total} items picked. New orders can be planned when it finishes.</span></p>
        </Panel>
      )}

      <Panel title="New order">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={500}
          placeholder="e.g. Pick 2 chips packets and 1 Frooti and bring them to the packing area"
          className="w-full resize-none rounded-lg border border-line-strong bg-raised px-3.5 py-3 text-[17px] leading-snug text-ink placeholder:text-dim focus:border-info focus:outline-none" />
        <div className="mt-2 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button key={ex} onClick={() => { setQty({}); setText(ex); }}
              className="min-h-10 rounded-full border border-line px-3 text-[13px] text-muted active:bg-raised">{ex}</button>
          ))}
        </div>
        <Button tone="primary" className="mt-3 w-full" busy={busy} disabled={!text.trim() || running || !!(m && m.status === "awaiting_confirmation")}
          onClick={submit}>
          <Send size={18} aria-hidden />Submit order
        </Button>
        {m?.status === "awaiting_confirmation" && (
          <p className="mt-2 text-center text-[13px] text-dim">Start or discard mission M{m.id} first.</p>
        )}
        <p className="mt-2 text-center text-[12px] text-dim">
          Parsed on the server by {snap.connections.ai.parser_model ?? "the offline keyword parser"}.
          Nothing moves until you confirm.
        </p>
      </Panel>

      <Panel title="Build by touch" right={<span className="text-[12px] text-dim">Products this robot can pick</span>}>
        <div className="grid grid-cols-3 gap-2">
          {catalog.map((c) => {
            const n = qty[c.id] ?? 0;
            const set = (v: number) => setQty({ ...qty, [c.id]: Math.max(0, Math.min(v, c.stock, 10)) });
            return (
              <div key={c.id} className={`rounded-lg border p-3 ${n ? "border-info/60 bg-info/5" : "border-line bg-raised/50"}`}>
                <div className="text-[15px] font-semibold leading-tight">{c.name}</div>
                <div className="mt-0.5 text-[12px] text-dim">{label(snap, c.shelf)} · {c.stock} in stock</div>
                <div className="mt-2.5 flex items-center justify-between">
                  <button onClick={() => set(n - 1)} disabled={!n} aria-label={`Fewer ${c.name}`}
                    className="flex h-11 w-11 items-center justify-center rounded-lg border border-line-strong active:bg-line disabled:opacity-30">
                    <Minus size={18} aria-hidden /></button>
                  <span className="num text-2xl font-bold">{n}</span>
                  <button onClick={() => set(n + 1)} disabled={n >= c.stock} aria-label={`More ${c.name}`}
                    className="flex h-11 w-11 items-center justify-center rounded-lg border border-line-strong active:bg-line disabled:opacity-30">
                    <Plus size={18} aria-hidden /></button>
                </div>
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

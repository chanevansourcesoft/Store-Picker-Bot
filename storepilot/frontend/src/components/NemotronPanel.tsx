import type { ReactNode } from "react";
import { Brain, Check, FileText, Hand, MessageSquareText, Route } from "lucide-react";
import { fmtTime, modelName, niceModels } from "../api";
import type { Snapshot } from "../types";
import { Panel } from "./ui";

type StepState = "waiting" | "active" | "done";

const STEP_STYLE: Record<StepState, string> = {
  waiting: "border-line bg-raised/40",
  active: "border-plan/70 bg-plan/10 shadow-[0_0_0_1px_var(--color-plan)]",
  done: "border-ok/40 bg-ok/5",
};

function Step({ n, title, model, icon, state, children }: {
  n: number; title: string; model: string; icon: ReactNode; state: StepState; children: ReactNode;
}) {
  return (
    <div className={`flex min-w-0 flex-col rounded-xl border p-3.5 ${STEP_STYLE[state]}`}>
      <div className="flex items-center gap-2.5">
        <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[13px] font-bold ${
          state === "done" ? "bg-ok text-bg" : state === "active" ? "bg-plan text-bg" : "bg-line text-muted"}`}>
          {state === "done" ? <Check size={16} aria-hidden /> : n}
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[14px] font-semibold text-ink">{icon}{title}</div>
          <div className="num truncate text-[11px] text-muted" title={model}>{model}</div>
        </div>
      </div>
      <div className="mt-3 min-h-0 flex-1 text-[13px] leading-snug text-muted">{children}</div>
    </div>
  );
}

/** Where NVIDIA Nemotron works in a mission: read the order, plan each step, (GR00T picks), write the report. */
export function NemotronPanel({ snap }: { snap: Snapshot }) {
  const ai = snap.connections.ai;
  const m = snap.mission;
  const online = !!ai.provider;
  const events = m ? snap.events.filter((e) => e.mission_id === m.id) : [];
  // the newest five, oldest first so they read like a plan
  const decisions = events.filter((e) => e.source === "ai" && / decided: /.test(e.text)).slice(0, 5).reverse();
  const picks = events.filter((e) => e.source === "robot" && e.text.includes("placed in basket"));
  const instruction = picks.length ? /GR00T instruction “(.+?)”/.exec(picks[0].text)?.[1] : undefined;

  const running = m?.status === "running";
  const finished = !!m && !["awaiting_confirmation", "running", "rejected", "discarded"].includes(m.status);
  const planned = !!m && m.status !== "rejected";

  const name = (used: string | null | undefined, model: string | null, fallback: string) =>
    used ? niceModels(used).replace(/^NVIDIA Nemotron \((.+)\)$/, "NVIDIA $1") : model ? `NVIDIA ${modelName(model)}` : fallback;
  const parsedBy = name(m?.parser, ai.parser_model, "offline keyword parser");
  const plannedBy = name(m?.planner, online ? ai.planner_model : null, "offline autopilot");
  const reportBy = name(m?.summary_by, ai.summary_model, "rule-based summary");

  return (
    <Panel title="Nemotron at work" icon={<Brain size={15} />}
      right={<span className="text-[12px] font-medium text-muted">
        {online ? `NVIDIA Nemotron on ${ai.provider}` : "Offline: no Nebius API key"}</span>}>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Step n={1} title="Understand the order" model={parsedBy} icon={<MessageSquareText size={15} aria-hidden />}
          state={m ? "done" : "waiting"}>
          {m ? (
            <>
              <p className="italic">“{m.text}”</p>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {m.items.map((i) => (
                  <li key={i.item} className="rounded-md border border-line-strong bg-panel px-2 py-0.5 text-[12px] font-semibold text-ink">
                    {i.qty}× {i.name}
                  </li>
                ))}
                {m.unknown.map((u) => (
                  <li key={u} className="rounded-md border border-warn/40 px-2 py-0.5 text-[12px] text-warn">{u}: not sold</li>
                ))}
              </ul>
            </>
          ) : "Type an order in plain language on the Order page. Nemotron Nano turns it into products and quantities."}
        </Step>

        <Step n={2} title="Plan and command the robot" model={plannedBy} icon={<Route size={15} aria-hidden />}
          state={running ? "active" : finished ? "done" : "waiting"}>
          {snap.robot.state === "planning" && (
            <p className="mb-2 flex items-center gap-1.5 font-medium text-plan">
              Deciding the next step<span className="thinking leading-none"><span>.</span><span>.</span><span>.</span></span>
            </p>
          )}
          {decisions.length ? (
            <ol className="flex flex-col gap-1.5">
              {decisions.map((e) => (
                <li key={e.id} className="flex gap-2">
                  <span className="num shrink-0 text-[11px] text-dim">{fmtTime(e.ts)}</span>
                  <span className="text-ink">{e.text.split(" decided: ")[1]}</span>
                </li>
              ))}
            </ol>
          ) : planned ? "Starts when the mission is confirmed. Each drive and pick is chosen by the planner and checked by safety rules before the robot gets it."
            : "Plans the route and sends the robot one command at a time."}
        </Step>

        <Step n={3} title="Pick with the arm" model="NVIDIA GR00T N1.7 (on the Jetson)" icon={<Hand size={15} aria-hidden />}
          state={running && snap.robot.state === "picking" ? "active" : finished && m!.packed > 0 ? "done" : "waiting"}>
          {m && m.total > 0 && (running || finished) ? (
            <>
              <p><span className="num text-lg font-bold text-ink">{m.packed}</span> of{" "}
                <span className="num text-lg font-bold text-ink">{m.total}</span> items in the basket</p>
              {instruction && <p className="mt-1.5">Last instruction: <span className="text-ink">“{instruction}”</span></p>}
            </>
          ) : "The planner only names the product. GR00T gets the exact instruction it was trained on and picks the item into the basket."}
        </Step>

        <Step n={4} title="Write the mission report" model={reportBy} icon={<FileText size={15} aria-hidden />}
          state={m?.summary ? "done" : finished ? "active" : "waiting"}>
          {m?.summary ? <p className="text-ink">{m.summary}</p>
            : finished ? <p className="font-medium text-plan">Writing the report…</p>
              : "When the mission ends, Nemotron Ultra writes a short report for the operator."}
        </Step>
      </div>
    </Panel>
  );
}

import type { ReactNode } from "react";
import {
  AlertOctagon, AlertTriangle, Bot, Brain, CheckCircle2, CircleDot, Info, Loader2, Navigation,
  PackageCheck, PauseCircle, Hand, Undo2, Unplug, User, Cog,
} from "lucide-react";
import type { Level, RobotState } from "../types";

/** One place that decides how each robot state looks (color + icon + label). */
export const STATE_META: Record<RobotState, { label: string; color: string; icon: typeof Bot; live?: boolean }> = {
  idle: { label: "Idle", color: "var(--color-muted)", icon: CircleDot },
  planning: { label: "Planning", color: "var(--color-plan)", icon: Brain, live: true },
  navigating: { label: "Navigating", color: "var(--color-info)", icon: Navigation, live: true },
  picking: { label: "Picking", color: "var(--color-warn)", icon: Hand, live: true },
  returning: { label: "Returning", color: "var(--color-teal)", icon: Undo2, live: true },
  completed: { label: "Completed", color: "var(--color-ok)", icon: PackageCheck },
  paused: { label: "Paused", color: "#fb923c", icon: PauseCircle },
  error: { label: "Error", color: "var(--color-err)", icon: AlertOctagon },
  offline: { label: "Disconnected", color: "var(--color-dim)", icon: Unplug },
};

export const LEVEL_COLOR: Record<Level, string> = {
  info: "var(--color-info)",
  success: "var(--color-ok)",
  warning: "var(--color-warn)",
  error: "var(--color-err)",
};

export function LevelIcon({ level, size = 16 }: { level: Level; size?: number }) {
  const Icon = { info: Info, success: CheckCircle2, warning: AlertTriangle, error: AlertOctagon }[level];
  return <Icon size={size} style={{ color: LEVEL_COLOR[level] }} className="shrink-0" aria-hidden />;
}

export const SOURCE_META = {
  robot: { label: "Robot", icon: Bot },
  ai: { label: "AI", icon: Brain },
  operator: { label: "Operator", icon: User },
  system: { label: "System", icon: Cog },
} as const;

export function Panel({ title, icon, right, children, className = "", bodyClass = "" }: {
  title?: ReactNode; icon?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.08em] text-muted">
            {icon}
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className={`p-4 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function StateBadge({ state, big = false }: { state: RobotState; big?: boolean }) {
  const meta = STATE_META[state];
  const Icon = meta.icon;
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-lg border font-bold uppercase tracking-[0.12em] ${
        big ? "px-3.5 py-2 text-lg" : "px-2 py-1 text-xs"
      }`}
      style={{ color: meta.color, borderColor: `color-mix(in srgb, ${meta.color} 45%, transparent)`,
        background: `color-mix(in srgb, ${meta.color} 12%, transparent)` }}
    >
      {meta.live ? (
        <span className="pulse-dot inline-block h-2.5 w-2.5 rounded-full" style={{ background: meta.color, color: meta.color }} />
      ) : (
        <Icon size={big ? 20 : 14} aria-hidden />
      )}
      {meta.label}
    </span>
  );
}

/** Small colored dot + label, for connection states. */
export function Dot({ color, pulse = false }: { color: string; pulse?: boolean }) {
  return (
    <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${pulse ? "pulse-dot" : ""}`}
      style={{ background: color, color }} aria-hidden />
  );
}

export function Tag({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "sim" | "ok" | "warn" | "err" | "info" }) {
  const styles = {
    neutral: "border-line-strong text-muted",
    sim: "border-warn/50 text-warn bg-warn/10",
    ok: "border-ok/40 text-ok bg-ok/10",
    warn: "border-warn/40 text-warn bg-warn/10",
    err: "border-err/40 text-err bg-err/10",
    info: "border-info/40 text-info bg-info/10",
  }[tone];
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${styles}`}>
      {children}
    </span>
  );
}

export function Button({ children, onClick, tone = "default", disabled, busy, className = "", type = "button" }: {
  children: ReactNode; onClick?: () => void; tone?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean; busy?: boolean; className?: string; type?: "button" | "submit";
}) {
  const styles = {
    default: "bg-raised border-line-strong text-ink active:bg-line",
    primary: "bg-ok/90 border-ok text-[#06140c] active:bg-ok",
    danger: "bg-err border-err text-white active:bg-[#d63e3b]",
    ghost: "bg-transparent border-line text-muted active:bg-raised",
  }[tone];
  return (
    <button type={type} onClick={onClick} disabled={disabled || busy}
      className={`inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border px-4 text-[15px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${styles} ${className}`}>
      {busy && <Loader2 size={18} className="animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function ProgressBar({ value, total, color = "var(--color-ok)" }: { value: number; total: number; color?: string }) {
  const pct = total ? Math.min(100, (100 * value) / total) : 0;
  return (
    <div className="h-2.5 w-full overflow-hidden rounded-full bg-raised" role="progressbar" aria-valuenow={value} aria-valuemax={total}>
      <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

/** Telemetry tile. value === null shows "Unavailable" (never a made-up number). */
export function Stat({ label, value, unit, icon, hint }: {
  label: string; value: ReactNode | null; unit?: string; icon?: ReactNode; hint?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-raised/60 px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-dim">
        {icon}
        {label}
      </div>
      {value === null || value === undefined ? (
        <div className="mt-1 text-[15px] font-medium text-dim">Unavailable</div>
      ) : (
        <div className={`num mt-0.5 truncate font-semibold text-ink ${String(value).length > 9 ? "text-[16px] leading-7" : "text-xl"}`}>
          {value}
          {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
        </div>
      )}
      {hint && <div className="mt-0.5 truncate text-[11px] text-dim">{hint}</div>}
    </div>
  );
}

export function Empty({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center text-sm text-dim">
      {icon}
      {children}
    </div>
  );
}

export const MISSION_STATUS: Record<string, { label: string; tone: "neutral" | "ok" | "warn" | "err" | "info" }> = {
  awaiting_confirmation: { label: "Awaiting confirmation", tone: "info" },
  running: { label: "Running", tone: "info" },
  completed: { label: "Completed", tone: "ok" },
  completed_with_issues: { label: "Completed · issues", tone: "warn" },
  failed: { label: "Failed", tone: "err" },
  aborted: { label: "Stopped", tone: "err" },
  interrupted: { label: "Interrupted", tone: "warn" },
  rejected: { label: "Rejected", tone: "err" },
  discarded: { label: "Discarded", tone: "neutral" },
  expired: { label: "Expired", tone: "neutral" },
};

export function MissionStatusTag({ status }: { status: string }) {
  const meta = MISSION_STATUS[status] ?? { label: status, tone: "neutral" as const };
  return <Tag tone={meta.tone}>{meta.label}</Tag>;
}

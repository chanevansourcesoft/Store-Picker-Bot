// Shapes sent by the backend (storepilot/backend/storepilot/main.py build_snapshot).
// Any telemetry value can be null: that means "not known", never zero.

export type RobotState =
  | "idle" | "planning" | "navigating" | "picking" | "returning"
  | "completed" | "paused" | "error" | "offline";

export type Level = "info" | "success" | "warning" | "error";

export interface Pose { x: number; y: number; theta: number }

export interface MissionItem { item: string; name: string; qty: number; shelf?: string | null; inferred?: boolean }

export interface Mission {
  id: number;
  created_at: string;
  text: string;
  mode: "mock" | "live";
  status: string;
  items: MissionItem[];
  unknown: string[];
  route: string[];
  parser: string | null;
  planner: string | null;
  confirmed_at: string | null;
  finished_at: string | null;
  packed: number;
  total: number;
  bill: { total?: number } | Record<string, never>;
  problems: string[];
  eta?: { total_sec: number; samples: number; remaining_sec?: number | null } | null;
}

export interface EventItem {
  id: number;
  ts: string;
  mission_id: number | null;
  source: "robot" | "ai" | "operator" | "system";
  level: Level;
  text: string;
  simulated: boolean;
}

export interface Snapshot {
  mode: "mock" | "live";
  server_time: number;
  safety: {
    motion_enabled: boolean;
    stop_connected: boolean;
    manipulation: "simulated" | "verified" | "unavailable";
    needs_area_confirmation: boolean;
    can_start: boolean;
    cannot_start_reason: string;
  };
  connections: {
    dashboard: {
      state: string; model: string | null; cpu_temp_c: number | null; load_1m: number; cpus: number;
      mem_used_pct: number | null; disk_free_gb: number; system_uptime_sec: number | null; dashboard_uptime_sec: number;
    };
    jetson: {
      state: "simulated" | "connected" | "disconnected" | "not_configured";
      url: string | null; latency_ms: number | null; ros_available: boolean | null;
      ros_connected: boolean | null; detail: string | null;
    };
    telemetry: { state: "simulated" | "live" | "stub" | "stale" | "unavailable"; age_sec: number | null };
    ai: {
      state: "ready" | "degraded" | "error" | "offline" | "unknown"; detail: string; provider: string | null;
      parser_model: string | null; planner_model: string | null; checked_at: number | null;
    };
  };
  robot: {
    state: RobotState; detail: string; pose: Pose | null; station: string | null; nav_state: string | null;
    battery: number | null; speed: number | null; holding: string | null; obstacle: boolean | null;
    stopped: boolean | null; busy: boolean | null; last_error: string | null; target: string | null;
    stop_pending: boolean;
    unverified: string[];
  };
  mission: Mission | null;
  map: { stations: Record<string, Pose> | null; source: "simulated" | "jetson" | null; labels: Record<string, string> };
  alerts: { level: Level; text: string }[];
  events: EventItem[];
}

export interface CatalogItem { id: string; name: string; shelf: string; shelf_name: string; stock: number; price: number }

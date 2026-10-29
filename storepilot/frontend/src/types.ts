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
  summary?: string | null;
  summary_by?: string | null;
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
    stop_verified: boolean;
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
      url: string | null; link?: string; latency_ms: number | null; ros_available: boolean | null;
      ros_connected: boolean | null; detail: string | null;
    };
    telemetry: { state: "simulated" | "live" | "stub" | "stale" | "unavailable"; age_sec: number | null };
    ai: {
      state: "ready" | "degraded" | "error" | "offline" | "unknown"; detail: string; provider: string | null;
      parser_model: string | null; planner_model: string | null; summary_model: string | null; checked_at: number | null;
    };
  };
  robot: {
    state: RobotState; detail: string; pose: Pose | null; station: string | null; nav_state: string | null;
    battery: number | null; speed: number | null; holding: string | null; obstacle: boolean | null;
    stopped: boolean | null; busy: boolean | null; last_error: string | null; target: string | null;
    picking_item?: string | null;
    stop_pending: boolean;
    unverified: string[];
    battery_v?: number | null;
    loc_quality?: number | null;
    odom_bad?: string | null;
    localized?: boolean | null;
    needs_initial_pose?: boolean | null;
    nav2_active?: boolean | null;
    goal?: { x: number; y: number; theta: number; status: string | null; distance?: number; station?: string | null } | null;
    map_name?: string | null;
  };
  ros: {
    map: { name: string | null; version: number; width: number; height: number; resolution: number;
      origin: { x: number; y: number } } | null;
    maps: string[];
    scan: [number, number][];
    plan: [number, number][];
    costmap?: { version: number; width: number; height: number; resolution: number;
      origin: { x: number; y: number; yaw: number } } | null;
    footprint?: [number, number][] | null;
    watchdog?: { armed: boolean; heartbeat_age?: number | null; goal_running?: boolean; cancels: number;
      last_reason?: string; timeout?: number } | null;
  } | null;
  jetson_ctl: {
    target: string; age_s: number | null; error: string | null; busy: string | null; amcl_recovery: boolean | null;
    status: { nav_running: boolean; map: string; speed: string; nav_uptime_s: string; rosbridge: boolean;
      watchdog: boolean; load: string; maps: { name: string; origin: number[] }[] } | null;
  } | null;
  drive: { available: boolean; motion_enabled: boolean; stop_verified: boolean; stop_verified_at?: string | null };
  route: {
    busy: boolean;
    settings: { auto_align: boolean; guard: boolean; amcl_recovery?: boolean; stop_verified_at?: string | null };
    route: {
      waypoints: { x: number; y: number; theta: number; label: string; station: string | null }[];
      index: number; state: string; message: string; realigns: number; finished: boolean;
    } | null;
  } | null;
  mission: Mission | null;
  map: { stations: Record<string, Pose> | null; source: "simulated" | "jetson" | null; labels: Record<string, string>;
    station_counts?: Record<string, number> | null };
  store: Record<string, Record<string, { id: string; name: string; stock: number }[]>>;
  shift_report: { text: string; insights?: Insight[]; by: string; at: string; missions: number } | null;
  media: { camera_url: string | null; featured_video: Video | null; upload_enabled: boolean };
  alerts: { level: Level; text: string }[];
  events: EventItem[];
}

export interface CatalogItem { id: string; name: string; shelf: string; shelf_name: string; level?: string | null; stock: number; price: number }

export interface Video { id: string; title: string; kind: "file" | "youtube"; url: string; size?: number | null; created_at: string }

export interface Insight { area: string; finding: string; action: string }

/** GET /api/analysis: today's exact store numbers (missions.py analysis()). */
export interface Analysis {
  date: string;
  orders: { placed: number; started: number; completed: number; completed_with_issues: number; failed: number;
    stopped: number; refused: number; refused_for_stock: number; waiting: number; success_rate: number | null;
    items_ordered: number; items_delivered: number };
  robot: { pick_attempts: number; missed_grasps: number; pick_success_rate: number | null; failed_drives: number;
    safety_blocks: number; average_mission_seconds: number | null };
  products: { id: string; name: string; shelf: string; level: string | null; stock: number; start: number;
    ordered_today: number; delivered_today: number; status: "ok" | "low" | "out"; restock: number }[];
  problems: string[];
}

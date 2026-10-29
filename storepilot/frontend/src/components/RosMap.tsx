import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { Crosshair, LocateFixed, Minus, Plus } from "lucide-react";
import type { Pose, Snapshot } from "../types";

export type PickMode = "view" | "goal" | "pose" | "route";
export type Picked = { x: number; y: number; theta: number };

const S = 100; // SVG units per meter

/**
 * RViz-like live view: the real Nav2 map (PNG from the Pi backend), LiDAR points, Nav2 plan,
 * robot pose, goal and saved stations. In "goal" / "pose" mode, press where the robot should be
 * and drag toward the heading (like RViz Nav2 Goal / 2D Pose Estimate), then release.
 */
export function RosMap({ snap, mode = "view", onPick, compact = false, draft = [], showCostmap = true, badPoints = [] }: {
  snap: Snapshot; mode?: PickMode; onPick?: (p: Picked) => void; compact?: boolean; draft?: Picked[];
  showCostmap?: boolean; badPoints?: number[];
}) {
  const ros = snap.ros;
  const m = ros?.map;
  const pose = snap.robot.pose;
  const svgRef = useRef<SVGSVGElement>(null);
  const [zoom, setZoom] = useState(1);
  const [center, setCenter] = useState<{ x: number; y: number } | null>(null);
  const [follow, setFollow] = useState(true);
  const [drag, setDrag] = useState<{ x: number; y: number; theta: number } | null>(null);
  const pan = useRef<{ px: number; py: number; cx: number; cy: number } | null>(null);

  // world size of the whole map
  const world = useMemo(() => m && {
    x0: m.origin.x, y0: m.origin.y, w: m.width * m.resolution, h: m.height * m.resolution,
  }, [m]);

  useEffect(() => { setCenter(null); setZoom(1); }, [m?.name, m?.version]);
  useEffect(() => { if (mode === "view") setDrag(null); }, [mode]);

  if (!m || !world) {
    return (
      <div className="flex aspect-[4/3] items-center justify-center rounded-lg border border-dashed border-line text-sm text-dim">
        {snap.connections.jetson.state === "connected" ? "Waiting for the map from Nav2 (map_server must be active)…" : "Map unavailable: robot disconnected"}
      </div>
    );
  }

  // visible window (meters), keeps the map's aspect ratio
  const viewW = world.w / zoom;
  const viewH = world.h / zoom;
  const fallback = { x: world.x0 + world.w / 2, y: world.y0 + world.h / 2 };
  const c = follow && pose && zoom > 1 ? pose : center ?? fallback;
  const vx = Math.min(Math.max(c.x - viewW / 2, world.x0), world.x0 + world.w - viewW);
  const vy = Math.min(Math.max(c.y - viewH / 2, world.y0), world.y0 + world.h - viewH);
  const X = (x: number) => (x - world.x0) * S;
  const Y = (y: number) => (world.y0 + world.h - y) * S;
  const deg = (t: number) => (-t * 180) / Math.PI;
  const k = 1 / zoom; // keep marks the same size on screen when zoomed

  function toWorld(e: PointerEvent) {
    const svg = svgRef.current!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    return { x: p.x / S + world!.x0, y: world!.y0 + world!.h - p.y / S };
  }

  function down(e: PointerEvent<SVGSVGElement>) {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const w = toWorld(e);
    if (mode === "view") {
      pan.current = { px: w.x, py: w.y, cx: vx + viewW / 2, cy: vy + viewH / 2 };
      setFollow(false);
    } else {
      setDrag({ x: w.x, y: w.y, theta: drag?.theta ?? 0 });
    }
  }
  function move(e: PointerEvent<SVGSVGElement>) {
    const w = toWorld(e);
    if (mode === "view" && pan.current) {
      const p = pan.current;
      setCenter({ x: p.cx - (w.x - p.px), y: p.cy - (w.y - p.py) });
      pan.current = { ...p, cx: p.cx - (w.x - p.px), cy: p.cy - (w.y - p.py) };
    } else if (drag && e.buttons) {
      const dx = w.x - drag.x, dy = w.y - drag.y;
      if (Math.hypot(dx, dy) > 0.08) setDrag({ ...drag, theta: Math.atan2(dy, dx) });
    }
  }
  function up() {
    pan.current = null;
    if (drag && mode !== "view") {
      onPick?.(drag);
      if (mode === "route") setDrag(null);
    }
  }
  const goal = snap.robot.goal;
  const goalActive = goal && (goal.status === "accepted" || goal.status === "executing");
  const plan = ros.plan.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ");
  const stations = snap.map.stations ?? {};
  const pickColor = mode === "pose" ? "var(--color-plan)" : mode === "route" ? "var(--color-brand)" : "var(--color-ok)";
  const active = snap.route?.busy ? snap.route.route : null;
  const routePts = active ? active.waypoints : draft;
  const routeLine = [...(pose && routePts.length ? [pose] : []), ...routePts]
    .map((p) => `${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ");

  return (
    <div className="relative">
      <svg ref={svgRef} viewBox={`${X(vx)} ${Y(vy + viewH)} ${viewW * S} ${viewH * S}`}
        className={`block w-full touch-none rounded-lg bg-[#0d131a] ${compact ? "max-h-[300px]" : "max-h-[calc(100vh-330px)]"} ${mode !== "view" ? "cursor-crosshair" : ""}`}
        style={{ aspectRatio: `${world.w} / ${world.h}` }}
        onPointerDown={compact ? undefined : down} onPointerMove={compact ? undefined : move} onPointerUp={compact ? undefined : up}
        role="img" aria-label="Live robot map">
        <image href={`/api/rosmap.png?v=${m.name}-${m.version}`} x={0} y={0} width={world.w * S} height={world.h * S}
          preserveAspectRatio="none" style={{ imageRendering: "pixelated" }} />

        {/* Nav2 local costmap (lives in odom; the backend placed it in the map frame) */}
        {showCostmap && ros.costmap && (
          <g transform={`translate(${X(ros.costmap.origin.x)} ${Y(ros.costmap.origin.y)}) rotate(${deg(ros.costmap.origin.yaw)})`}>
            <image href={`/api/costmap.png?v=${ros.costmap.version}`} x={0} y={-ros.costmap.height * ros.costmap.resolution * S}
              width={ros.costmap.width * ros.costmap.resolution * S} height={ros.costmap.height * ros.costmap.resolution * S}
              preserveAspectRatio="none" style={{ imageRendering: "pixelated" }} />
          </g>
        )}

        {/* LiDAR */}
        <g fill="#f0524f">
          {ros.scan.map(([x, y], i) => <rect key={i} x={X(x) - 2.5 * k} y={Y(y) - 2.5 * k} width={5 * k} height={5 * k} />)}
        </g>

        {/* Nav2 plan */}
        {plan && <polyline points={plan} fill="none" stroke="var(--color-ok)" strokeWidth={4 * k} strokeLinejoin="round" opacity={0.9} />}

        {/* saved stations */}
        {Object.entries(stations).map(([name, s]) => (
          <g key={name} transform={`translate(${X(s.x)} ${Y(s.y)})`}>
            <circle r={9 * k} fill="var(--color-brand)" stroke="#0d131a" strokeWidth={3 * k} />
            <text y={-15 * k} textAnchor="middle" fontSize={(compact ? 20 : 15) * k} fontWeight={700} fill="#ffd59a">
              {(snap.map.labels[name] ?? name).toUpperCase()}
            </text>
          </g>
        ))}

        {/* route: draft waypoints, or the running route (done = dim, current = blue) */}
        {routePts.length > 0 && (
          <polyline points={routeLine} fill="none" stroke="var(--color-brand)" strokeWidth={3 * k}
            strokeDasharray={`${10 * k} ${8 * k}`} opacity={0.7} />
        )}
        {routePts.map((p, i) => {
          const done = active && i < active.index;
          const current = active && i === active.index;
          const color = badPoints.includes(i) ? "var(--color-err)" : current ? "var(--color-info)" : done ? "#3a4757" : "var(--color-brand)";
          return (
            <g key={`wp${i}`} transform={`translate(${X(p.x)} ${Y(p.y)})`}>
              <path d={`M 0 0 L ${30 * k * Math.cos(p.theta)} ${-30 * k * Math.sin(p.theta)}`} stroke={color} strokeWidth={4 * k} />
              <circle r={14 * k} fill="#0d131a" stroke={color} strokeWidth={4 * k} />
              <text y={5 * k} textAnchor="middle" fontSize={15 * k} fontWeight={800} fill={color}>{i + 1}</text>
            </g>
          );
        })}

        {/* goal */}
        {goal && goalActive && (
          <g transform={`translate(${X(goal.x)} ${Y(goal.y)}) rotate(${deg(goal.theta)})`}>
            <circle r={14 * k} fill="none" stroke="var(--color-info)" strokeWidth={4 * k} />
            <path d={`M ${12 * k} 0 L ${34 * k} 0`} stroke="var(--color-info)" strokeWidth={4 * k} />
          </g>
        )}

        {/* the arrow being placed */}
        {drag && mode !== "view" && (
          <g transform={`translate(${X(drag.x)} ${Y(drag.y)}) rotate(${deg(drag.theta)})`}>
            <circle r={16 * k} fill="none" stroke={pickColor} strokeWidth={4 * k} />
            <path d={`M 0 0 L ${46 * k} 0 M ${34 * k} ${-9 * k} L ${46 * k} 0 L ${34 * k} ${9 * k}`} stroke={pickColor}
              strokeWidth={5 * k} fill="none" strokeLinecap="round" />
          </g>
        )}

        {/* robot: real footprint from Nav2 when available */}
        {pose && <Robot pose={pose} X={X} Y={Y} k={k} />}
        {ros.footprint && ros.footprint.length > 2 && (
          <polygon points={ros.footprint.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ")}
            fill="none" stroke="var(--color-teal)" strokeWidth={2.5 * k} strokeDasharray={`${6 * k} ${4 * k}`} />
        )}
      </svg>

      <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-ok/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-ok ring-1 ring-ok/30">
        Live · {m.name ?? "map"}
      </div>
      {!pose && (
        <div className="pointer-events-none absolute inset-x-0 top-2 mx-auto w-fit rounded-md bg-bg/80 px-2.5 py-1 text-[12px] font-semibold text-warn">
          Robot not localized
        </div>
      )}
      {!compact && (
        <div className="absolute right-2 top-2 flex flex-col gap-1.5">
          {[
            { icon: Plus, label: "Zoom in", on: () => setZoom((z) => Math.min(z * 1.6, 12)) },
            { icon: Minus, label: "Zoom out", on: () => { setZoom((z) => Math.max(z / 1.6, 1)); } },
            { icon: LocateFixed, label: "Follow robot", on: () => { setFollow(true); setZoom((z) => Math.max(z, 2.5)); } },
            { icon: Crosshair, label: "Whole map", on: () => { setZoom(1); setCenter(null); setFollow(true); } },
          ].map(({ icon: Icon, label, on }) => (
            <button key={label} onClick={on} aria-label={label}
              className="flex h-11 w-11 items-center justify-center rounded-lg border border-line-strong bg-panel/90 text-ink active:bg-raised">
              <Icon size={20} aria-hidden />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Robot({ pose, X, Y, k }: { pose: Pose; X: (x: number) => number; Y: (y: number) => number; k: number }) {
  return (
    <g transform={`translate(${X(pose.x)} ${Y(pose.y)}) rotate(${(-pose.theta * 180) / Math.PI})`}
      style={{ transition: "transform 250ms linear" }}>
      <circle r={30 * k} fill="var(--color-info)" opacity={0.15} />
      <rect x={-16 * k} y={-13 * k} width={32 * k} height={26 * k} rx={6 * k} fill="#1d2a38" stroke="var(--color-ink)" strokeWidth={3 * k} />
      <path d={`M ${2 * k} ${-8 * k} L ${17 * k} 0 L ${2 * k} ${8 * k} Z`} fill="var(--color-brand)" />
    </g>
  );
}

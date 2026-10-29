import type { Pose, Snapshot } from "../types";

const S = 100; // SVG units per meter

/** Label spot: just past the far end of the shelf / packing-area box, or under home. */
function labelAt(name: string, p: Pose) {
  if (name === "home") return { x: p.x, y: p.y - 0.5 };
  const fx = p.x + Math.cos(p.theta) * 0.5;
  const fy = p.y + Math.sin(p.theta) * 0.5;
  const [along, across] = name === "customer" ? [0.24, 0.55] : [0.24, 0.62];
  const top = Math.abs(Math.sin(p.theta)) * along + Math.abs(Math.cos(p.theta)) * across;
  return { x: fx, y: fy + top + 0.16 };
}
const PAD = 0.9; // meters around the content

/** Where the robot is in its route: stops already done, and the one it works on now. */
export function routeProgress(snap: Snapshot) {
  const m = snap.mission;
  const active = m && (m.status === "running" || m.status === "awaiting_confirmation");
  if (!m || !active) return { stops: [] as string[], current: -1 };
  const stops = [...m.route, "customer", "home"];
  let current = 0;
  if (m.status === "running") {
    const target = snap.robot.target;
    const here = snap.robot.station;
    const doneShelves = new Set<string>();
    // a shelf is done once the robot left it for a later stop
    if (target && stops.includes(target)) current = stops.indexOf(target);
    else if (here && stops.includes(here)) current = stops.indexOf(here);
    stops.slice(0, current).forEach((s) => doneShelves.add(s));
  }
  return { stops, current };
}

/** fill: take the parent's height and fit the whole map inside it (no scrolling), centered. */
export function StoreMap({ snap, compact = false, fill = false }: { snap: Snapshot; compact?: boolean; fill?: boolean }) {
  // Only the tables that hold products (or are in the route): the store has one table with two shelves
  const inUse = new Set(Object.keys(snap.store ?? {}));
  const routeStops = routeProgress(snap).stops;
  const stations = snap.map.stations && Object.fromEntries(Object.entries(snap.map.stations).filter(([name]) =>
    !name.startsWith("shelf") || inUse.size === 0 || inUse.has(name) || routeStops.includes(name)));
  const labels = snap.map.labels;
  const pose = snap.robot.pose;
  if (!stations || Object.keys(stations).length === 0) {
    return (
      <div className="flex aspect-[4/3] items-center justify-center rounded-lg border border-dashed border-line text-sm text-dim">
        Map unavailable: no station positions from the robot
      </div>
    );
  }

  const pts = Object.values(stations).map((p) => ({ x: p.x + Math.cos(p.theta) * 0.5, y: p.y + Math.sin(p.theta) * 0.5 }))
    .concat(Object.values(stations));
  if (pose) pts.push(pose);
  const minX = Math.min(...pts.map((p) => p.x)) - PAD;
  const maxX = Math.max(...pts.map((p) => p.x)) + PAD;
  const minY = Math.min(...pts.map((p) => p.y)) - PAD;
  const maxY = Math.max(...pts.map((p) => p.y)) + PAD;
  const W = (maxX - minX) * S;
  const H = (maxY - minY) * S;
  const X = (x: number) => (x - minX) * S;
  const Y = (y: number) => (maxY - y) * S;
  const deg = (theta: number) => (-theta * 180) / Math.PI;
  const font = compact ? 22 : 15;

  const { stops, current } = routeProgress(snap);
  const running = snap.mission?.status === "running";
  const target = snap.robot.target;
  const at = (s: string) => stations[s];
  const done = running && stops.length ? ["home", ...stops.slice(0, Math.max(current, 0))].filter(at) : [];
  const upcoming = stops.slice(Math.max(current, 0)).filter(at);
  const pathOf = (names: string[], start?: Pose | null) =>
    [...(start ? [start] : []), ...names.map(at)].map((p) => `${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ");

  const grid = [];
  for (let gx = Math.ceil(minX * 2) / 2; gx <= maxX; gx += 0.5) grid.push(<line key={`x${gx}`} x1={X(gx)} x2={X(gx)} y1={0} y2={H} />);
  for (let gy = Math.ceil(minY * 2) / 2; gy <= maxY; gy += 0.5) grid.push(<line key={`y${gy}`} y1={Y(gy)} y2={Y(gy)} x1={0} x2={W} />);

  return (
    <div className={`relative ${fill ? "h-full" : ""}`}>
      <svg viewBox={`0 0 ${W.toFixed(0)} ${H.toFixed(0)}`}
        className={`block w-full rounded-lg bg-[#0d131a] ${fill ? "h-full" : "h-auto"}`}
        role="img" aria-label="Store map with robot position">
        <g stroke="#1a232e" strokeWidth={1}>{grid}</g>

        {/* stations */}
        {Object.entries(stations).map(([name, p]) => {
          const isShelf = name.startsWith("shelf");
          const isTarget = name === target || (!target && running && stops[current] === name);
          const inRoute = stops.includes(name);
          const fx = p.x + Math.cos(p.theta) * 0.5;
          const fy = p.y + Math.sin(p.theta) * 0.5;
          const accent = isTarget ? "var(--color-info)" : inRoute ? "var(--color-brand)" : "#3a4757";
          return (
            <g key={name}>
              {isShelf && (
                <g transform={`translate(${X(fx)} ${Y(fy)}) rotate(${deg(p.theta)})`}>
                  {/* the table seen from above; its two shelf levels are drawn in the front view */}
                  <rect x={-24} y={-62} width={48} height={124} rx={6} fill={isTarget ? "#132a40" : "#1b1712"}
                    stroke={accent} strokeWidth={isTarget ? 4 : 2.5} />
                  <rect x={-16} y={-54} width={32} height={108} rx={3} fill="none" stroke="#3a3127" strokeWidth={2} strokeDasharray="5 5" />
                </g>
              )}
              {name === "customer" && (
                <g transform={`translate(${X(fx)} ${Y(fy)}) rotate(${deg(p.theta)})`}>
                  <rect x={-24} y={-55} width={48} height={110} rx={6} fill="#13261d" stroke={isTarget ? "var(--color-info)" : "var(--color-ok)"}
                    strokeWidth={isTarget ? 4 : 2.5} strokeDasharray={isTarget ? undefined : "8 6"} />
                </g>
              )}
              {name === "home" && (
                <circle cx={X(p.x)} cy={Y(p.y)} r={30} fill="none" stroke={isTarget ? "var(--color-info)" : "#3a4757"}
                  strokeWidth={2.5} strokeDasharray="6 6" />
              )}
              <circle cx={X(p.x)} cy={Y(p.y)} r={isTarget ? 8 : 6} fill={accent} />
              <text x={X(labelAt(name, p).x)} y={Y(labelAt(name, p).y)} textAnchor="middle" fontSize={font}
                dominantBaseline="middle"
                fontWeight={700} fill={isTarget ? "#cfe6ff" : "#8b98a6"} style={{ letterSpacing: "0.04em" }}>
                {(labels[name] ?? name).toUpperCase()}
              </text>
            </g>
          );
        })}

        {/* route: done legs solid, upcoming dashed, the current leg animated */}
        {done.length > 1 && <polyline points={pathOf(done)} fill="none" stroke="#2f3b49" strokeWidth={5} strokeLinejoin="round" />}
        {upcoming.length > 0 && (running || snap.mission?.status === "awaiting_confirmation") && (
          <>
            <polyline points={pathOf(upcoming, running ? pose : at("home"))} fill="none" stroke="var(--color-info)" strokeOpacity={0.35}
              strokeWidth={4} strokeDasharray="10 10" strokeLinejoin="round" />
            {running && pose && at(upcoming[0]) && (
              <polyline points={pathOf([upcoming[0]], pose)} fill="none" stroke="var(--color-info)" strokeWidth={5}
                strokeDasharray="14 10" className="route-active" strokeLinecap="round" />
            )}
          </>
        )}

        {/* robot */}
        {pose && (
          <g style={{ transition: "transform 250ms linear" }} transform={`translate(${X(pose.x)} ${Y(pose.y)})`}>
            <circle r={34} fill="var(--color-info)" opacity={0.12} />
            <g style={{ transition: "transform 250ms linear" }} transform={`rotate(${deg(pose.theta)})`}>
              <rect x={-20} y={-17} width={40} height={34} rx={8} fill="#1d2a38" stroke="var(--color-ink)" strokeWidth={3} />
              <path d="M 4 -10 L 20 0 L 4 10 Z" fill="var(--color-brand)" />
            </g>
          </g>
        )}
      </svg>

      <div className="pointer-events-none absolute bottom-2 left-2 flex gap-1.5">
        {snap.map.source === "simulated" ? (
          <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-warn ring-1 ring-warn/40">
            Simulated map
          </span>
        ) : (
          <span className="rounded bg-ok/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-ok ring-1 ring-ok/30">
            Jetson stations
          </span>
        )}
      </div>
      {!pose && (
        <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-bg/60 text-sm font-medium text-muted">
          Robot position unavailable
        </div>
      )}
      <div className="pointer-events-none absolute bottom-2 right-2 flex items-center gap-1.5 text-[10px] text-dim">
        <span className="inline-block h-1 rounded bg-dim" style={{ width: `${(S / W) * 100}%`, minWidth: 18 }} />1 m
      </div>
    </div>
  );
}

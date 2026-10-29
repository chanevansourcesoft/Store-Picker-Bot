import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, WifiOff } from "lucide-react";
import { useSnapshot } from "./api";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SimBanner, TabNav, TopBar, type Tab } from "./components/Shell";
import { OpsPage } from "./pages/OpsPage";
import { OrderPage } from "./pages/OrderPage";
import { MissionPage } from "./pages/MissionPage";
import { SystemPage } from "./pages/SystemPage";
import { VideosPage } from "./pages/VideosPage";

type Toast = { id: number; text: string; tone: "ok" | "err" | "warn" };

export default function App() {
  const { snapshot: snap, link } = useSnapshot();
  const [tab, setTab] = useState<Tab>("ops");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const frozenAt = useRef<string>("");

  const toast = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-2), { id, text, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "err" ? 9000 : 5000);
  }, []);

  useEffect(() => {
    if (link === "live") frozenAt.current = new Date().toLocaleTimeString([], { hour12: false });
  }, [link, snap]);

  // Scroll back to top when switching pages
  const main = useRef<HTMLElement>(null);
  useEffect(() => { main.current?.scrollTo({ top: 0 }); }, [tab]);

  const hasError = !!snap?.alerts.some((a) => a.level === "error");

  return (
    <div className="flex h-full flex-col">
      <TopBar snap={snap} link={link} toast={toast} />
      <TabNav tab={tab} setTab={setTab} badges={{ ops: hasError && tab !== "ops",
        order: snap?.mission?.status === "awaiting_confirmation" && tab !== "order",
        mission: snap?.mission?.status === "running" && tab !== "mission" }} />
      {snap && <SimBanner snap={snap} />}
      {link === "lost" && (
        <div className="flex shrink-0 items-center justify-center gap-2 bg-err py-1.5 text-[13px] font-semibold text-white">
          <WifiOff size={16} aria-hidden />
          Dashboard backend unreachable — reconnecting.{snap && ` Data below is frozen since ${frozenAt.current}.`}
        </div>
      )}

      <main ref={main} className={`scroll-y min-h-0 flex-1 p-3 transition-opacity lg:p-5 ${link === "lost" ? "opacity-50" : ""}`}>
        <div className="mx-auto h-full max-w-[1500px]">
        <ErrorBoundary resetKey={tab}>
        {!snap ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-muted">
            <Loader2 className="animate-spin" size={32} aria-hidden />
            Connecting to the StorePilot backend…
          </div>
        ) : tab === "ops" ? (
          <OpsPage snap={snap} goto={setTab} toast={toast} />
        ) : tab === "order" ? (
          <OrderPage snap={snap} toast={toast} goto={setTab} />
        ) : tab === "mission" ? (
          <MissionPage snap={snap} toast={toast} />
        ) : tab === "videos" ? (
          <VideosPage snap={snap} toast={toast} />
        ) : (
          <SystemPage snap={snap} toast={toast} />
        )}
        </ErrorBoundary>
        </div>
      </main>

      <div className="pointer-events-none fixed right-4 top-[124px] z-50 flex w-[min(440px,calc(100%-2rem))] flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} role="status"
            className={`pointer-events-auto rounded-lg border px-4 py-3 text-[14px] font-medium shadow-2xl shadow-black/60 ${
              t.tone === "err" ? "border-err/60 bg-[#2a1314] text-[#ffb4b2]"
                : t.tone === "warn" ? "border-warn/60 bg-[#2a2010] text-[#ffd59a]"
                  : "border-ok/50 bg-[#0f2318] text-[#a6efc6]"}`}>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}

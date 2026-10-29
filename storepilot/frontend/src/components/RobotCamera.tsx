import { useState } from "react";
import { ArrowRight, Clapperboard, Video as VideoIcon } from "lucide-react";
import type { Tab } from "./Shell";
import { Panel, Tag } from "./ui";
import type { Snapshot, Video } from "../types";

/** A YouTube embed that starts muted and loops, so it can play by itself. */
export function youtubeSrc(v: Video, autoplay: boolean) {
  const id = v.url.split("/").pop();
  return autoplay ? `${v.url}?autoplay=1&mute=1&loop=1&playlist=${id}&rel=0` : `${v.url}?rel=0`;
}

export function VideoPlayer({ v, autoplay = false }: { v: Video; autoplay?: boolean }) {
  if (v.kind === "youtube") {
    return (
      <iframe src={youtubeSrc(v, autoplay)} title={v.title} className="aspect-video w-full rounded-lg bg-black"
        allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen />
    );
  }
  return (
    <video src={v.url} className="aspect-video w-full rounded-lg bg-black" controls playsInline preload="metadata"
      {...(autoplay ? { autoPlay: true, muted: true, loop: true } : {})} />
  );
}

/** Live robot camera when a real robot is connected, otherwise the newest real robot video. */
export function RobotCamera({ snap, goto }: { snap: Snapshot; goto: (t: Tab) => void }) {
  const { camera_url, featured_video } = snap.media;
  const [cameraFailed, setCameraFailed] = useState(false);
  const live = !!camera_url && !cameraFailed;

  return (
    <Panel title={live ? "Robot camera" : "Real robot"} icon={live ? <VideoIcon size={15} /> : <Clapperboard size={15} />}
      right={live ? (
        <span className="flex items-center gap-1.5 text-[12px] font-bold uppercase tracking-wider text-err">
          <span className="pulse-dot inline-block h-2 w-2 rounded-full bg-err" />Live
        </span>
      ) : featured_video ? <Tag tone="info">Recorded on the real robot</Tag> : null}
      bodyClass="p-2">
      {live ? (
        <img src={camera_url!} alt="Live view from the robot camera" onError={() => setCameraFailed(true)}
          className="aspect-video w-full rounded-lg bg-black object-contain" />
      ) : featured_video ? (
        <>
          <VideoPlayer v={featured_video} autoplay />
          <div className="flex items-center justify-between gap-3 px-2 pb-1 pt-2">
            <span className="truncate text-[14px] font-medium text-ink">{featured_video.title}</span>
            <button onClick={() => goto("videos")}
              className="flex shrink-0 items-center gap-1 text-[13px] font-semibold text-muted hover:text-ink">
              All videos <ArrowRight size={14} aria-hidden />
            </button>
          </div>
        </>
      ) : (
        <div className="flex aspect-video flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-line text-center">
          <Clapperboard size={30} className="text-dim" aria-hidden />
          <p className="max-w-xs text-[14px] text-muted">
            {cameraFailed ? "The live robot camera is not reachable." : "No real robot video yet."}
          </p>
          <button onClick={() => goto("videos")} className="text-[13px] font-semibold text-info hover:underline">
            Open the Real robot page
          </button>
        </div>
      )}
    </Panel>
  );
}

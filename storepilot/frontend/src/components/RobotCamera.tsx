import type { Video } from "../types";

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

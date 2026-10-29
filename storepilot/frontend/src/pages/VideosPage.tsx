import { useCallback, useEffect, useState } from "react";
import { Clapperboard, Link2, Lock, Trash2, Upload, Video as VideoIcon } from "lucide-react";
import { api, useTeamPassword } from "../api";
import { VideoPlayer } from "../components/RobotCamera";
import { Button, Panel } from "../components/ui";
import type { Snapshot, Video } from "../types";

type Toast = (t: string, tone?: "ok" | "err" | "warn") => void;

const field = "w-full rounded-lg border border-line-strong bg-raised px-3 py-2.5 text-[15px] text-ink placeholder:text-dim focus:border-info focus:outline-none";

function fmtDate(iso: string) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

/** Upload with progress (fetch cannot report upload progress). */
function uploadFile(file: File, title: string, password: string, onProgress: (pct: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/videos/upload?title=${encodeURIComponent(title)}`);
    xhr.setRequestHeader("content-type", file.type || "application/octet-stream");
    xhr.setRequestHeader("x-upload-password", password);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((100 * e.loaded) / e.total));
    xhr.onload = () => {
      if (xhr.status < 300) return resolve();
      let detail = `Upload failed (HTTP ${xhr.status})`;
      try { detail = JSON.parse(xhr.responseText).detail ?? detail; } catch { /* not JSON */ }
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error("Upload failed: the server could not be reached."));
    xhr.send(file);
  });
}

function TeamTools({ maxMb, password, setPassword, reload, toast }: {
  maxMb: number; password: string; setPassword: (p: string) => void; reload: () => void; toast: Toast;
}) {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [linkTitle, setLinkTitle] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);

  async function upload() {
    if (!file) return;
    if (file.size > maxMb * 1024 * 1024) return toast(`The video is larger than ${maxMb} MB.`, "err");
    setProgress(0);
    try {
      await uploadFile(file, title, password, setProgress);
      toast("Video uploaded", "ok");
      setTitle(""); setFile(null); reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setProgress(null);
    }
  }

  async function addLink() {
    setBusy(true);
    try {
      await api("/api/videos/link", { title: linkTitle, url: link, password });
      toast("YouTube video added", "ok");
      setLinkTitle(""); setLink(""); reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <label className="block">
        <span className="mb-1 block text-[12px] font-semibold uppercase tracking-wider text-dim">Team password</span>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={field}
          placeholder="Needed to add or delete videos" autoComplete="current-password" />
      </label>

      <div className="rounded-lg border border-line p-3">
        <h3 className="mb-2 flex items-center gap-2 text-[14px] font-semibold"><Upload size={15} aria-hidden />Upload a video file</h3>
        <input value={title} onChange={(e) => setTitle(e.target.value)} className={field} placeholder="Title, e.g. Robot picks a biscuit" maxLength={120} />
        <input type="file" accept="video/mp4,video/webm,video/quicktime,video/ogg" className="mt-2 block w-full text-[14px] text-muted file:mr-3 file:rounded-md file:border-0 file:bg-raised file:px-3 file:py-2 file:text-ink"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <p className="mt-1 text-[12px] text-dim">MP4, WebM or MOV, up to {maxMb} MB.</p>
        {progress !== null && (
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-raised"><div className="h-full bg-info transition-[width]" style={{ width: `${progress}%` }} /></div>
        )}
        <Button tone="primary" className="mt-2 w-full" busy={progress !== null} disabled={!file || !title.trim() || !password} onClick={upload}>
          <Upload size={17} aria-hidden />{progress !== null ? `Uploading ${progress}%` : "Upload"}
        </Button>
      </div>

      <div className="rounded-lg border border-line p-3">
        <h3 className="mb-2 flex items-center gap-2 text-[14px] font-semibold"><Link2 size={15} aria-hidden />Add a YouTube link</h3>
        <input value={linkTitle} onChange={(e) => setLinkTitle(e.target.value)} className={field} placeholder="Title" maxLength={120} />
        <input value={link} onChange={(e) => setLink(e.target.value)} className={`${field} mt-2`} placeholder="https://youtu.be/…" />
        <Button className="mt-2 w-full" busy={busy} disabled={!link.trim() || !linkTitle.trim() || !password} onClick={addLink}>
          <Link2 size={17} aria-hidden />Add link
        </Button>
      </div>
    </div>
  );
}

export function VideosPage({ snap, toast }: { snap: Snapshot; toast: Toast }) {
  const [videos, setVideos] = useState<Video[] | null>(null);
  const [maxMb, setMaxMb] = useState(500);
  const [password, setPassword] = useTeamPassword();

  const reload = useCallback(() => {
    api<{ videos: Video[]; max_mb: number }>("/api/videos")
      .then((r) => { setVideos(r.videos); setMaxMb(r.max_mb); })
      .catch((e) => toast((e as Error).message, "err"));
  }, [toast]);
  const featuredId = snap.media.featured_video?.id;
  useEffect(reload, [reload, featuredId]);

  async function remove(v: Video) {
    if (!window.confirm(`Delete “${v.title}”?`)) return;
    try {
      await api(`/api/videos/${v.id}/delete`, { password });
      toast("Video deleted", "warn");
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }

  const team = snap.media.upload_enabled;
  return (
    <div className="grid items-start gap-3 lg:grid-cols-12 lg:gap-4">
      <div className="flex flex-col gap-3 lg:col-span-8 lg:gap-4">
        {snap.media.camera_url && (
          <Panel title="Robot camera" icon={<VideoIcon size={15} />} bodyClass="p-2"
            right={<span className="flex items-center gap-1.5 text-[12px] font-bold uppercase tracking-wider text-err">
              <span className="pulse-dot inline-block h-2 w-2 rounded-full bg-err" />Live</span>}>
            <img src={snap.media.camera_url} alt="Live view from the robot camera"
              className="aspect-video w-full rounded-lg bg-black object-contain" />
          </Panel>
        )}
        <Panel title="The real robot" icon={<Clapperboard size={15} />}>
          <p className="text-[15px] text-muted">
            Recordings of the real Store-Picker-Bot: a mobile base with an SO-100 arm, running NVIDIA GR00T N1.7 on a
            Jetson AGX Orin, with missions planned by NVIDIA Nemotron.
            {snap.mode === "mock" && " The live demo on this site drives a simulated robot with the same software."}
          </p>
        </Panel>

        {videos === null ? (
          <p className="py-10 text-center text-sm text-dim">Loading videos…</p>
        ) : videos.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-line py-14 text-center">
            <Clapperboard size={32} className="text-dim" aria-hidden />
            <p className="text-[15px] text-muted">No videos yet.</p>
            {team && <p className="text-[13px] text-dim">Add one with the team tools.</p>}
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 lg:gap-4">
            {videos.map((v) => (
              <article key={v.id} className="rounded-xl border border-line bg-panel p-2">
                <VideoPlayer v={v} />
                <div className="flex items-start justify-between gap-2 px-2 pb-1 pt-2">
                  <div className="min-w-0">
                    <h3 className="truncate text-[15px] font-semibold text-ink">{v.title}</h3>
                    <p className="text-[12px] text-dim">
                      {fmtDate(v.created_at)}{v.kind === "youtube" ? " · YouTube" : v.size ? ` · ${(v.size / 1048576).toFixed(0)} MB` : ""}
                    </p>
                  </div>
                  {team && password && (
                    <button onClick={() => remove(v)} aria-label={`Delete ${v.title}`}
                      className="rounded-md p-2 text-dim hover:bg-raised hover:text-err"><Trash2 size={16} aria-hidden /></button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      <div className="lg:col-span-4">
        <Panel title="Team: add videos" icon={<Lock size={15} />}>
          {team ? (
            <TeamTools maxMb={maxMb} password={password} setPassword={setPassword} reload={reload} toast={toast} />
          ) : (
            <p className="text-[14px] text-muted">
              Adding videos is switched off on this server. To turn it on, set <span className="num text-ink">STOREPILOT_UPLOAD_PASSWORD</span> in
              {" "}<span className="num text-ink">storepilot/.env</span> and restart.
            </p>
          )}
        </Panel>
      </div>
    </div>
  );
}

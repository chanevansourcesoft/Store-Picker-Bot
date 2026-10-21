// Store Picker Bot episode player: both camera videos kept in sync, a frame slider, playback speed
// and joint plots. LeRobot keeps many episodes in one .mp4 per camera, so each episode is a time
// range [from, to) of its file; the player only shows that range.
// The host page provides the elements and the CSS (.videos, .vbox, .plot, ...) and the colour
// variables --muted, --state, --action and --cursor.

class EpisodePlayer {
  static CAM_LABELS = { scene_cam: "Scene camera", gripper_cam: "Gripper camera" };

  // el: {videos, slider, frameLabel, playBtn, speed, plots} (DOM elements)
  constructor(el, { onError } = {}) {
    this.el = el;
    this.onError = onError || (() => {});
    this.ep = null;
    this.joints = null;
    this.videos = [];  // [{v, from, to}]
    this.panels = [];
    this.lastCursor = -1;
    el.playBtn.onclick = () => this.togglePlay();
    el.slider.oninput = (e) => this.seek(Number(e.target.value));
    el.speed.onchange = (e) => { for (const x of this.videos) x.v.playbackRate = Number(e.target.value); };
    window.addEventListener("resize", () => this.drawPlots());
    const loop = () => { this.tick(); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  static css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  get frame() { return Number(this.el.slider.value); }
  get playing() { return this.videos.length && !this.videos[0].v.paused; }

  // ep: an episode row from /api/episodes ({index, frames, fps, videos: {cam: {url, from, to}}})
  async show(ep) {
    this.ep = ep;
    this.el.slider.max = ep.frames - 1;
    this.el.slider.value = 0;
    this.buildVideos();
    this.joints = null;
    this.el.plots.innerHTML = "";
    try {
      const r = await fetch(`/api/joints/${ep.index}`);
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.statusText);
      if (this.ep && this.ep.index === ep.index) { this.joints = j; this.buildPlots(); }
    } catch (err) { this.onError("Could not load the joints: " + err.message); }
  }

  clear() {
    for (const x of this.videos) { x.v.pause(); x.v.removeAttribute("src"); x.v.load(); }
    this.videos = [];
    this.ep = null;
    this.joints = null;
    this.el.videos.innerHTML = "";
    this.el.plots.innerHTML = "";
  }

  buildVideos() {
    for (const x of this.videos) { x.v.pause(); x.v.removeAttribute("src"); x.v.load(); }
    const box = this.el.videos;
    box.innerHTML = "";
    this.videos = Object.entries(this.ep.videos).map(([cam, info]) => {
      const wrap = document.createElement("div");
      wrap.className = "vbox";
      wrap.innerHTML = `<div class="wait">Loading video…</div><span class="cap">${EpisodePlayer.CAM_LABELS[cam] || cam}</span>`;
      const v = document.createElement("video");
      v.muted = true; v.playsInline = true; v.preload = "auto";
      v.playbackRate = Number(this.el.speed.value);
      v.addEventListener("loadedmetadata", () => { v.currentTime = info.from + 0.5 / this.ep.fps; }, { once: true });
      v.addEventListener("seeked", () => wrap.querySelector(".wait")?.remove(), { once: true });
      v.addEventListener("error", () => { const w = wrap.querySelector(".wait"); if (w) w.textContent = "Could not load this video."; });
      v.src = info.url;
      wrap.prepend(v);
      box.appendChild(wrap);
      return { v, from: info.from, to: info.to };
    });
    this.el.playBtn.textContent = "Play";
  }

  frameOf(t) { return Math.max(0, Math.min(this.ep.frames - 1, Math.floor(t * this.ep.fps + 1e-3))); }

  seek(f) {
    if (!this.ep || !this.videos.length) return;
    f = Math.max(0, Math.min(this.ep.frames - 1, f));
    for (const x of this.videos) { x.v.pause(); x.v.currentTime = x.from + (f + 0.5) / this.ep.fps; }
    this.el.playBtn.textContent = "Play";
  }

  step(n) { this.seek(this.frame + n); }

  togglePlay() {
    if (!this.videos.length) return;
    if (!this.playing) {
      if (this.frame >= this.ep.frames - 1) this.seek(0);
      for (const x of this.videos) x.v.play().catch(() => {});
      this.el.playBtn.textContent = "Pause";
    } else {
      for (const x of this.videos) x.v.pause();
      this.el.playBtn.textContent = "Play";
    }
  }

  tick() {
    if (!this.ep || !this.videos.length) return;
    const main = this.videos[0];
    const t = main.v.currentTime - main.from;
    const f = this.frameOf(t);
    if (this.playing && main.v.currentTime >= main.to - 0.5 / this.ep.fps) {  // end of this episode's range
      for (const x of this.videos) x.v.pause();
      this.el.playBtn.textContent = "Play";
    }
    for (const x of this.videos.slice(1)) {
      if (Math.abs(x.v.currentTime - x.from - t) > 1.5 / this.ep.fps) x.v.currentTime = x.from + t;
    }
    if (document.activeElement !== this.el.slider) this.el.slider.value = f;
    this.el.frameLabel.textContent = `frame ${f} / ${this.ep.frames - 1} · ${(f / this.ep.fps).toFixed(2)} s`;
    this.drawCursors(f);
  }

  // Space, ←/→ (Shift: 10 frames). True if handled.
  handleKey(ev) {
    const step = ev.shiftKey ? 10 : 1;
    const keys = { " ": () => this.togglePlay(), ArrowLeft: () => this.step(-step), ArrowRight: () => this.step(step) };
    if (!keys[ev.key]) return false;
    ev.preventDefault();
    keys[ev.key]();
    return true;
  }

  // ---------- joint plots ----------
  buildPlots() {
    const box = this.el.plots;
    box.innerHTML = "";
    this.panels = [];
    this.joints.names.forEach((name, j) => {
      const p = document.createElement("div");
      p.className = "plot" + (name === "gripper" ? " gripper" : "");
      p.innerHTML = `<div class="pname"><span>${name}</span><span class="val"></span></div><div class="stack"><canvas></canvas><canvas></canvas></div>`;
      box.appendChild(p);
      const [base, over] = p.querySelectorAll("canvas");
      over.addEventListener("click", (ev) => {
        const r = over.getBoundingClientRect();
        this.seek(Math.round(((ev.clientX - r.left) / r.width) * (this.ep.frames - 1)));
      });
      this.panels.push({ j, base, over, val: p.querySelector(".val") });
    });
    this.drawPlots();
  }

  static sizeCanvas(c) {
    const dpr = window.devicePixelRatio || 1, r = c.getBoundingClientRect();
    c.width = Math.max(1, Math.round(r.width * dpr));
    c.height = Math.max(1, Math.round(r.height * dpr));
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return [ctx, r.width, r.height];
  }

  drawPlots() {
    if (!this.joints) return;
    const css = EpisodePlayer.css;
    for (const p of this.panels) {
      const s = this.joints.state[p.j], a = this.joints.action[p.j], n = s.length;
      let lo = Math.min(...s, ...a), hi = Math.max(...s, ...a);
      if (hi - lo < 1) { hi += 0.5; lo -= 0.5; }
      const padY = (hi - lo) * 0.08;
      lo -= padY; hi += padY;
      const [ctx, w, h] = EpisodePlayer.sizeCanvas(p.base);
      EpisodePlayer.sizeCanvas(p.over);
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = css("--muted");
      ctx.font = "10px system-ui";
      ctx.fillText(hi.toFixed(0), 2, 10);
      ctx.fillText(lo.toFixed(0), 2, h - 3);
      const line = (vals, color, dash) => {
        ctx.beginPath(); ctx.setLineDash(dash); ctx.strokeStyle = color; ctx.lineWidth = 1.5;
        vals.forEach((v, i) => { const x = (i / Math.max(n - 1, 1)) * w, y = h - ((v - lo) / (hi - lo)) * h; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
        ctx.stroke(); ctx.setLineDash([]);
      };
      line(s, css("--state"), []);
      line(a, css("--action"), [5, 3]);
    }
    this.drawCursors(this.frame, true);
  }

  drawCursors(f, force = false) {
    if (!this.joints || (!force && f === this.lastCursor)) return;
    this.lastCursor = f;
    for (const p of this.panels) {
      const ctx = p.over.getContext("2d"), r = p.over.getBoundingClientRect(), n = this.joints.state[p.j].length;
      ctx.clearRect(0, 0, r.width, r.height);
      const x = (f / Math.max(n - 1, 1)) * r.width;
      ctx.strokeStyle = EpisodePlayer.css("--cursor"); ctx.globalAlpha = 0.6; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, r.height); ctx.stroke(); ctx.globalAlpha = 1;
      const fi = Math.min(f, n - 1);
      p.val.textContent = `follower ${this.joints.state[p.j][fi].toFixed(1)} · leader ${this.joints.action[p.j][fi].toFixed(1)}`;
    }
  }
}

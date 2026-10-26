// Store Picker Bot front-end. Talks to studio.py; the episode player comes from /static/player.js.
const $ = (id) => document.getElementById(id);
const STEPS = [["campaign", "Campaign"], ["check", "Check"], ["record", "Record"], ["review", "Review"], ["train", "Train"]];
const REASONS = ["Dropped the product", "Wrong product", "Product not in the basket", "Did not end in the rest pose",
  "Hit the shelf or the basket", "Long pause or hesitation", "Person or hand in the camera view", "Other"];
const CAMS = { scene_cam: "Scene camera", gripper_cam: "Gripper camera" };

let S = null;              // last /api/studio snapshot
let confirmed = false;     // a campaign was opened since this window opened
let page = null;           // current page id
let episodes = [];         // dataset episodes (review page)
let reviewTab = "todo";
let current = null;        // episode shown in the review
let selectedCampaign = null;
let armTimer = null;
let armState = null;
let checksOk = false;
let prevRecording = false;
let prevFinishing = false;
let restAction = null;     // what the rest-pose dialog confirms
let T = null;              // last /api/train answer (the training PC)
let selectedRun = null;
let prevSending = false;

const player = new EpisodePlayer({
  videos: $("videos"), slider: $("slider"), frameLabel: $("frameLabel"), playBtn: $("playBtn"),
  speed: $("speed"), plots: $("plots"),
}, { onError: (m) => toast(m) });

// ---------- helpers ----------
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, ms = 4000) {
  const t = $("toast"); t.textContent = msg; t.style.display = "block";
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.style.display = "none"), ms);
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(4, "0");
const pretty = (item) => String(item || "?").replace(/_/g, " ");
function kv(rows) { return rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join(""); }
const job = () => S && S.record;
const recording = () => !!(job() && job().status === "running");
const finishing = () => !!(S && S.finish && S.finish.status === "running");
const phase = () => (recording() ? job().info.phase : null);
const camp = () => S && S.campaign;

// ---------- steps ----------
function stepInfo(id) {
  if (!confirmed || !camp()) return { open: id === "campaign", done: false };
  const c = camp();
  switch (id) {
    case "campaign": return { open: true, done: true };
    case "check": return { open: true, done: S.arm_test_passed };
    case "record": return { open: true, done: !S.next };
    case "review": return { open: !c.draft && c.episodes > 0, done: !c.draft && c.episodes > 0 && !c.to_review, badge: c.to_review };
    case "train": return { open: S.training_configured && !c.draft && c.episodes > 0, done: trainDone() };
    default: return { open: false, done: false };
  }
}

function renderSteps() {
  $("steps").innerHTML = STEPS.map(([id, label], i) => {
    const s = stepInfo(id);
    const cls = ["step", s.open ? "" : "locked", s.done ? "done" : "", page === id ? "current" : ""].join(" ");
    return `<button class="${cls}" data-step="${id}" ${s.open ? "" : "disabled"}><span class="n">${s.done ? "✓" : i + 1}</span>` +
      `<span class="t">${label}</span>${s.badge ? `<span class="b">${s.badge}</span>` : ""}</button>`;
  }).join("");
  $("steps").querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => go(b.dataset.step)));
}

function renderTop() {
  $("switchBtn").hidden = !confirmed || page === "campaign" || recording();
  const c = camp();
  $("who").innerHTML = confirmed && c
    ? `Campaign <b>${esc(c.name)}</b>: ${c.done} / ${c.total} episodes` : "";
}

function go(id) {
  if (!stepInfo(id).open) return;
  if (page === "check" && id !== "check") stopArmTest(false);
  if (page === "review" && id !== "review") { player.clear(); current = null; }
  page = id;
  history.replaceState(null, "", `#${id}`);
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("show", p.id === `page-${id}`));
  renderSteps();
  renderTop();
  enter(id);
}

function autoPage() {
  const c = camp();
  if (!confirmed || !c) return "campaign";
  if (recording()) return "record";
  if (c.to_review || (!S.next && c.episodes)) return "review";
  if (!S.arm_test_passed) return "check";
  return "record";
}

function enter(id) {
  if (id === "campaign") enterCampaign();
  if (id === "check") enterCheck();
  if (id === "record") renderRecord();
  if (id === "review") loadEpisodes(true);
  if (id === "train") enterTrain();
}

// ---------- refresh loop ----------
async function refresh() {
  try { S = await api("/api/studio"); } catch (e) { toast("Store Picker Bot is not responding: " + e.message); return; }
  if (!page) {
    if (recording()) { confirmed = true; go("record"); return; }  // the window was reopened during a round
    const wanted = location.hash.slice(1);
    go(STEPS.some(([id]) => id === wanted) && stepInfo(wanted).open ? wanted : autoPage());
    return;
  }
  if (prevRecording && !recording()) roundEnded();
  if (prevFinishing && !finishing()) {
    toast(S.finish.status === "finished" ? "Flagged episodes removed." : "Removing the flagged episodes failed: see the message.", 6000);
    if (page === "review") loadEpisodes(true);
  }
  if (prevSending && !sending()) sendEnded();
  prevRecording = recording();
  prevFinishing = finishing();
  prevSending = sending();
  renderTop();
  renderSteps();
  if (page === "record") renderRecord();
  if (page === "check") renderRoundInfo();
  if (page === "review") renderPlanComplete();
  if (page === "train") renderSend();
}

function roundEnded() {
  const j = job(), info = j.info;
  $("restModal").hidden = true;
  if (info.round_done) {
    toast(info.plan_complete ? "Last round complete. Review it now." : "Round complete. Review it now.", 6000);
    if (camp() && camp().episodes) go("review");
  } else if (j.status === "failed") {
    toast("The recording stopped with an error: see the message on the Record page.", 8000);
    if (page !== "record") go("record");
  } else {
    toast(`Round paused. ${info.saved} episode(s) saved in this session.`, 6000);
  }
}

$("switchBtn").onclick = () => go("campaign");

// ---------- 1. campaign ----------
function enterCampaign() {
  const rows = [...S.campaigns];
  const c = camp();
  if (c && c.draft) rows.unshift({ folder: "__draft__", draft: true, name: c.name, done: 0, total: c.total, products: c.products.map((p) => p.item), to_review: 0,
    rounds: c.rounds, per_round: c.per_round, rounds_started: 0, max_rounds: Math.max(...S.round_choices) });
  const active = selectedCampaign || (c && (c.draft ? "__draft__" : c.folder));
  $("campaignList").innerHTML = rows.length ? rows.map((r) => {
    const name = r.draft ? `${r.name} (not started yet)` : r.folder;
    const sub = `${r.done} / ${r.total} episodes · ${r.products.map(pretty).join(", ")}${r.to_review ? ` · ${r.to_review} to review` : ""}`;
    const last = c && (r.folder === c.folder || (r.draft && c.draft));
    return `<div class="item ${r.folder === active ? "active" : ""}" data-f="${esc(r.folder)}"><span class="name">${esc(name)}</span>` +
      `<span class="muted small">${last ? "last used" : ""}</span><span class="sub">${esc(sub)}</span></div>`;
  }).join("") : `<p class="muted" style="padding:12px">No campaign yet: create one on the right.</p>`;
  $("campaignList").querySelectorAll("[data-f]").forEach((el) => (el.onclick = () => { selectedCampaign = el.dataset.f; enterCampaign(); }));
  const sel = rows.find((r) => r.folder === active);
  $("campaignOpen").hidden = !sel;
  if (sel) {
    $("campaignOpenTitle").textContent = sel.draft ? sel.name : sel.folder;
    const e = $("editRounds");
    if (e.dataset.f !== sel.folder) { e.value = sel.rounds; e.dataset.f = sel.folder; }
    e.min = Math.max(1, sel.rounds_started); e.max = sel.max_rounds;
    e.oninput = () => renderRoundsNote(sel);
    renderRoundsNote(sel);
  }
  // new campaign form
  $("newName").placeholder = S.next_name;
  if (!$("newProducts").children.length) {
    $("newProducts").innerHTML = S.catalog.map((p) => `<label><input type="checkbox" value="${esc(p.item)}" checked>` +
      `<span>${esc(pretty(p.item))}</span><span class="shelf">${esc(p.shelf)}</span><span class="ins">"${esc(p.instruction)}"</span></label>`).join("");
    $("newRounds").innerHTML = S.round_choices.map((r) => `<option value="${r}">${r}</option>`).join("");
    $("newRounds").value = S.config.default_rounds;
    $("newPerRound").value = S.config.default_per_round;
    $("newOffset").value = S.config.default_offset_cm;
    $("newTurn").value = S.config.default_turn_deg;
    $("newProducts").querySelectorAll("input").forEach((i) => (i.onchange = renderNewTotal));
    [$("newRounds"), $("newPerRound")].forEach((i) => (i.oninput = renderNewTotal));
  }
  renderNewTotal();
}
function renderRoundsNote(sel) {
  const r = Number($("editRounds").value), n = sel.products.length, p = sel.per_round;
  const started = sel.rounds_started ? `Rounds 1–${sel.rounds_started} have episodes and stay. ` : "";
  $("roundsNote").textContent = `${started}Episodes per product per round stay at ${p}. ` +
    (r && r !== sel.rounds ? `${sel.rounds} → ${r} rounds: ${n} product(s) × ${r} × ${p} = ${n * r * p} episodes.` : `Now ${sel.rounds} rounds, ${sel.total} episodes.`);
  $("saveRoundsBtn").disabled = !r || r === sel.rounds;
}
$("saveRoundsBtn").onclick = async () => {
  const folder = $("editRounds").dataset.f;
  try {
    await api("/api/campaign/rounds", { folder, rounds: Number($("editRounds").value) });
    S = await api("/api/studio");
    toast("Rounds changed.");
    enterCampaign();
  } catch (e) { toast(e.message, 6000); }
};
const chosenProducts = () => [...$("newProducts").querySelectorAll("input:checked")].map((i) => i.value);
function renderNewTotal() {
  const n = chosenProducts().length, r = Number($("newRounds").value), p = Number($("newPerRound").value);
  $("newTotal").textContent = n ? `${n} product(s) × ${r} round(s) × ${p} episodes = ${n * r * p} episodes.` : "Choose at least one product.";
}
$("openCampaignBtn").onclick = async () => {
  const folder = selectedCampaign || (camp() && (camp().draft ? "__draft__" : camp().folder));
  try {
    if (folder !== "__draft__") await api("/api/campaign/open", { folder });
    S = await api("/api/studio");
    selectedCampaign = null;
    confirmed = true;
    go(autoPage());
  } catch (e) { toast(e.message); }
};
$("deleteCampaignBtn").onclick = () => {
  const folder = selectedCampaign || (camp() && (camp().draft ? "__draft__" : camp().folder));
  const row = S.campaigns.find((r) => r.folder === folder);
  if (folder === "__draft__") {
    askRest("Delete this campaign?", "It has not recorded anything yet: only its plan is forgotten.", "Delete campaign",
      () => deleteCampaign(folder), true);
  } else if (row) {
    askRest(`Delete ${folder}?`, `Its ${row.done} recorded episode(s) are moved to the Trash of this computer. ` +
      "They can be restored from the Trash until it is emptied.", "Move to the Trash", () => deleteCampaign(folder), true);
  }
};
async function deleteCampaign(folder) {
  try {
    await api("/api/campaign/delete", { folder });
    S = await api("/api/studio");
    selectedCampaign = null;
    if (!camp()) confirmed = false;
    toast(folder === "__draft__" ? "Campaign deleted." : `${folder} moved to the Trash.`, 5000);
    go("campaign");
  } catch (e) { toast(e.message, 7000); }
}
$("newCampaignBtn").onclick = async () => {
  try {
    await api("/api/campaign/new", {
      name: $("newName").value.trim(), products: chosenProducts(), rounds: Number($("newRounds").value),
      per_round: Number($("newPerRound").value), offset_cm: Number($("newOffset").value), turn_deg: Number($("newTurn").value),
    });
    S = await api("/api/studio");
    $("newName").value = "";
    selectedCampaign = null;
    confirmed = true;
    go("check");
  } catch (e) { toast(e.message, 6000); }
};

// ---------- 2. check ----------
async function enterCheck() {
  renderRoundInfo();
  renderCameraTest(S.camera_test);
  renderArms(null);
  await loadChecks();
}
async function loadChecks() {
  $("checks").innerHTML = `<p class="muted">Checking…</p>`;
  try {
    const { checks } = await api("/api/checks");
    checksOk = checks.every((c) => c.ok);
    $("checks").innerHTML = checks.map((c) => `<div class="check ${c.ok ? "ok" : "bad"}"><span class="ic">${c.ok ? "✓" : "✕"}</span>` +
      `<span>${esc(c.label)}</span><span class="detail">${esc(c.detail)}</span>${c.ok ? "" : `<span class="fix">${esc(c.fix)}</span>`}</div>`).join("");
  } catch (e) { checksOk = false; $("checks").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  updateToRecord();
}
$("recheckBtn").onclick = loadChecks;

function renderCameraTest(result) {
  $("camTest").innerHTML = Object.entries(CAMS).map(([cam, label]) => {
    const r = result && result.cameras[cam];
    const shot = r && r.ok ? `<img src="/camera_test/${cam}.jpg?t=${result.taken}" alt="">` : `<span>${r ? esc(r.message) : "No picture yet"}</span>`;
    return `<figure><div class="shot">${shot}</div><figcaption class="${r && !r.ok ? "bad" : ""}">${label}${r && r.ok ? " ✓" : r ? " ✕" : ""}</figcaption></figure>`;
  }).join("");
  $("camTestMsg").textContent = result && result.settings_error ? result.settings_error : "";
}
$("camTestBtn").onclick = async () => {
  $("camTestBtn").disabled = true;
  $("camTestMsg").textContent = "Taking pictures…";
  try { renderCameraTest(await api("/api/cameras/test", {})); } catch (e) { $("camTestMsg").textContent = e.message; }
  $("camTestBtn").disabled = false;
};

function renderArms(st) {
  armState = st;
  const f = st && st.follower;
  $("followerCheck").className = "check " + (f ? (f.ok ? "ok" : "bad") : "");
  $("followerCheck").innerHTML = `<span class="ic">${f ? (f.ok ? "✓" : "✕") : "·"}</span><span>Follower arm answers</span>` +
    `<span class="detail"></span>${f && !f.ok ? `<span class="fix">${esc(f.message)}</span>` : ""}`;
  const joints = st ? st.joints : ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"].map((name) => ({ name }));
  $("motors").innerHTML = joints.map((j) => {
    const lo = j.name === "gripper" ? 0 : -100, hi = 100;
    const pos = j.value == null ? null : Math.max(0, Math.min(100, ((j.value - lo) / (hi - lo)) * 100));
    return `<div class="motor ${j.moved ? "ok" : ""}"><span>${j.name.replace("_", " ")}</span>` +
      `<span class="gauge">${pos == null ? "" : `<span style="left:calc(${pos}% - 1px)"></span>`}</span>` +
      `<span class="val">${j.value == null ? "–" : j.value.toFixed(0)}</span><span class="ic">${j.moved ? "✓" : "·"}</span></div>`;
  }).join("");
  if (st) {
    const still = st.joints.filter((j) => !j.moved).map((j) => j.name.replace("_", " "));
    $("armMsg").textContent = st.error ? st.error
      : st.passed ? "Both arms work."
      : st.running && st.joints.every((j) => j.value == null) ? "Reading the leader arm…"
      : still.length ? `Move: ${still.join(", ")}` : f && !f.ok ? "Fix the follower arm, then test again." : "";
  } else $("armMsg").textContent = S && S.arm_test_passed ? "Test passed." : "";
  $("armStartBtn").textContent = st && st.running ? "Stop test" : "Start test";
  updateToRecord();
}
async function pollArms() {
  try {
    const st = await api("/api/arms");
    renderArms(st);
    if (!st.running) { clearInterval(armTimer); armTimer = null; }
  } catch (e) { clearInterval(armTimer); armTimer = null; }
}
async function stopArmTest(render = true) {
  clearInterval(armTimer); armTimer = null;
  try { await api("/api/arms/stop", {}); } catch {}
  if (render) { S = await api("/api/studio"); renderArms(null); renderSteps(); }
}
$("armStartBtn").onclick = async () => {
  if (armState && armState.running) return stopArmTest(true);
  try {
    await api("/api/arms/start", {});
    clearInterval(armTimer);
    armTimer = setInterval(pollArms, 200);
  } catch (e) { toast(e.message); }
};

function nextRoundText() {
  const n = S.next;
  if (!n) return "The plan is complete.";
  return `Next: <b>${esc(n.shelf)}</b>, round ${n.round} of ${n.rounds}: put the base <b>${esc(n.where)}</b>. ` +
    `${n.size - n.done} episode(s) to record.`;
}
function renderRoundInfo() {
  const c = camp();
  if (!c) return;
  let msg;
  if (recording()) msg = "A round is running.";
  else if (c.to_review) msg = `Review the last round first (${c.to_review} episode(s) waiting).`;
  else msg = nextRoundText();
  $("roundInfo").innerHTML = msg;
  updateToRecord();
}
function updateToRecord() {
  const c = camp();
  const armOk = (armState && armState.passed) || (S && S.arm_test_passed);
  $("toRecordBtn").disabled = !(c && (recording() || (checksOk && armOk && !c.to_review && S.next)));
}
$("toRecordBtn").onclick = async () => {
  await stopArmTest(false);  // ends the arm test; the server remembers that it passed
  S = await api("/api/studio");
  go("record");
};

// ---------- 3. record ----------
const PHASES = {
  starting: ["Starting…", "warn", "Opening the arms and cameras. The follower arm moves to the leader arm's pose."],
  connecting: ["Connecting to the arms…", "warn", "The follower arm moves to the leader arm's pose."],
  ready: ["Getting ready…", "warn", ""],
  waiting: ["Ready for the next episode", "ok", "Leader arm in the rest pose, then press Start (Space) just before you move."],
  recording: ["Recording", "rec", "Press Save (N) once the arm is back in the rest pose, or Cancel (X)."],
  saving: ["Saving…", "warn", "Wait until it says Ready. Meanwhile put the product back on the shelf."],
  done: ["Round complete", "ok", "Put the follower arm in its rest pose, then press the button below. Its motors then switch off."],
};
let liveTimer = null;  // runs while the live view is on
function renderRecord() {
  const c = camp();
  if (!c) return;
  const j = job(), running = recording(), ph = phase(), info = (j && j.info) || {};
  let title, cls, sub;
  if (running && j.stopping) [title, cls, sub] = ["Stopping…", "warn", "The episode in progress is dropped; saved episodes are kept."];
  else if (running) [title, cls, sub] = PHASES[ph] || PHASES.starting;
  else if (j && j.status === "failed") [title, cls, sub] = ["Stopped with an error", "bad", info.error || "See the message below. Fix it, then start the round again."];
  else if (!S.next) [title, cls, sub] = ["The plan is complete", "ok", "Review the last episodes on the Review step."];
  else if (c.to_review) [title, cls, sub] = ["Review first", "warn", `${c.to_review} episode(s) of the last round are waiting on the Review step.`];
  else if (S.next.done) [title, cls, sub] = ["Ready to resume the round", "ok", `${S.next.done} of ${S.next.size} episodes of this round are recorded. Keep the base where it says on the right, put the leader arm in its rest pose, then press Resume round.`];
  else [title, cls, sub] = ["Ready to start a round", "ok", "Put the base where it says on the right, the leader arm in its rest pose, then press Start round."];
  $("recState").textContent = title; $("recState").className = "state " + cls; $("recSub").textContent = sub;

  // timer
  const t = $("recTimer");
  if (running && ph === "recording") {
    const s = info.elapsed || 0;
    t.textContent = `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
    t.className = "timer " + (s > S.config.long_episode_s ? "long" : "rec");
    if (s > S.config.long_episode_s) $("recSub").textContent = "Long episode: press Save (N) or Cancel (X).";
  } else { t.textContent = ""; t.className = "timer"; }

  // buttons
  $("startBtn").disabled = !(running && ph === "waiting" && !j.stopping);
  $("saveBtn").disabled = $("discardBtn").disabled = !(running && ph === "recording" && !j.stopping);
  $("startRoundBtn").hidden = running || !S.next || !!c.to_review;
  $("startRoundBtn").textContent = S.next && S.next.done ? "Resume round" : "Start round";  // part of it is recorded
  $("finishRoundBtn").hidden = !(running && ph === "done" && !j.stopping);
  $("pauseRoundBtn").hidden = !running || ph === "done" || j.stopping;
  $("recLog").hidden = !(j && j.status === "failed");
  if (j && j.status === "failed") $("recLog").textContent = j.log.join("\n");

  // what to do now
  const slot = running && info.slot && ph !== "done" ? info.slot : null;
  const n = S.next;
  const base = slot || n;
  const roundOver = running && ph === "done";
  $("nowBase").className = "now-block" + (!running && n && !n.done ? " changed" : "");
  if (roundOver) {
    $("nowBase").innerHTML = `<div class="lbl">Base</div><div class="big">Round complete</div>` +
      `<div class="muted small">${n ? `After the review, the next round is at ${esc(n.shelf)}: ${esc(n.where)}.` : "That was the last round."}</div>`;
  } else {
    $("nowBase").innerHTML = base
      ? `<div class="lbl">Base · ${esc(base.shelf)} · round ${base.round} of ${base.rounds}</div><div class="big">${esc(base.where)}</div>` +
        `<div class="muted small">${running ? "Do not move the base during the round." : n && n.done ? "Part of this round is recorded: the base must still be here." : "Drive or push the base there now, using the floor marks."}</div>`
      : `<div class="lbl">Base</div><div class="big">All rounds are recorded</div>`;
  }
  $("nowProduct").hidden = roundOver || !(slot || n);
  if (slot) {
    $("nowProduct").innerHTML = `<div class="lbl">Pick this product${slot.item_shelf ? ` · ${esc(slot.item_shelf)}` : ""}</div><div class="big">${esc(pretty(slot.item))}</div>` +
      `<div class="muted">"${esc(slot.instruction)}"</div><div class="small">Episode ${slot.count} of ${slot.per_round} for this product in this round</div>` +
      `<div class="lbl vary">Before each episode, change the setup (mix freely)</div>` +
      `<ul class="small vary">${S.variations.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`;
  } else if (n) {
    $("nowProduct").innerHTML = `<div class="lbl">Products in this round</div>` +
      n.items.map((i) => `<div>${esc(pretty(i.item))}${i.shelf ? ` <span class="muted small">(${esc(i.shelf)})</span>` : ""}: <b>${i.done} / ${i.need}</b></div>`).join("");
  }
  const rd = slot ? slot.round_done : n ? n.done : 0, rs = slot ? slot.round_size : n ? n.size : 0;
  $("roundBar").style.width = roundOver || !rs ? "100%" : `${Math.min(100, (100 * rd) / rs)}%`;
  const rows = [["This round", roundOver ? "complete" : rs ? `${rd} / ${rs}` : "–"], ["Whole plan", `${slot ? slot.done : c.done} / ${c.total}`]];
  if (running) rows.push(["Saved now", info.saved], ["Cancelled", info.discarded]);
  $("nowCounts").innerHTML = kv(rows);

  // live view
  const live = running && !["starting", "connecting"].includes(ph);
  $("live").classList.toggle("rec", running && ph === "recording");
  if (live && !liveTimer) {
    for (const w of ["liveSceneWait", "liveGripperWait"]) $(w).textContent = "Waiting for the camera…";
    for (const [cam, img] of LIVE) $(img).src = `/live/${cam}.mjpg?t=${Date.now()}`;  // one stream per camera
    liveTimer = setInterval(checkLive, 1000);
  }
  if (!live && liveTimer) { clearInterval(liveTimer); liveTimer = null; stopLive(); }
}
const LIVE = [["scene_cam", "liveScene", "liveSceneWait"], ["gripper_cam", "liveGripper", "liveGripperWait"]];
function stopLive() {
  for (const [, img, wait] of LIVE) {
    $(img).removeAttribute("src");  // closes the stream
    $(wait).hidden = false; $(wait).textContent = "Live view starts with the round";
  }
}
for (const [cam, img] of LIVE) {  // a dropped stream reconnects while the round runs
  $(img).onerror = () => setTimeout(() => { if (liveTimer) $(img).src = `/live/${cam}.mjpg?t=${Date.now()}`; }, 1000);
}
async function checkLive() {
  try {  // a camera whose picture stops changing is shown as stopped
    const ages = await api("/api/live");
    for (const [cam, , wait] of LIVE) {
      const age = ages[cam];
      if (age != null && age <= 3) $(wait).hidden = true;
      else if (age != null) { $(wait).hidden = false; $(wait).textContent = "Camera stopped"; }
    }
  } catch {}
}

async function command(cmd) {
  try { await api("/api/record/cmd", { cmd }); S = await api("/api/studio"); renderRecord(); } catch (e) { toast(e.message); }
}
$("startBtn").onclick = () => command("start");
$("saveBtn").onclick = () => command("save");
$("discardBtn").onclick = () => command("discard");
$("startRoundBtn").onclick = async () => {
  $("startRoundBtn").disabled = true;
  try {
    await api("/api/record/start", {});
    S = await api("/api/studio");
    prevRecording = true;
    renderRecord();
  } catch (e) { toast(e.message, 7000); }
  $("startRoundBtn").disabled = false;
};
function askRest(title, text, ok, action, danger = false) {
  $("restTitle").textContent = title; $("restText").textContent = text; $("restOkBtn").textContent = ok;
  $("restOkBtn").className = danger ? "danger" : "primary";
  $("restCancelBtn").textContent = danger ? "Cancel" : "Not yet";
  restAction = action;
  $("restModal").hidden = false;
}
$("restOkBtn").onclick = async () => {
  $("restModal").hidden = true;
  if (restAction) await restAction();
};
$("restCancelBtn").onclick = () => ($("restModal").hidden = true);
async function stopRound() {
  try { await api("/api/record/stop", {}); toast("Stopping…"); } catch (e) { toast(e.message); }
}
$("finishRoundBtn").onclick = () => askRest("Finish the round?",
  "The follower arm's motors switch off: it must rest in its rest pose, or be held, so it does not drop.",
  "The arm is at rest: finish", stopRound);
$("pauseRoundBtn").onclick = () => askRest("Pause the round?",
  "Saved episodes are kept and you can continue later; an episode that is still recording is dropped. " +
  "The follower arm's motors switch off: put it in its rest pose, or hold it, first.", "Pause the round", stopRound);

// ---------- 4. review ----------
const isTodo = (e) => e.in_log && !e.approved && !e.bad;
async function loadEpisodes(selectFirst = false) {
  renderPlanComplete();
  try { episodes = (await api("/api/episodes")).episodes; } catch (e) { episodes = []; renderReviewList(); showReviewEmpty(e.message); return; }
  renderReviewList();
  if (selectFirst || !current || !episodes.some((e) => e.index === current.index)) {
    const first = visibleEpisodes()[0] || episodes.find(isTodo);
    if (first) select(first.index); else showReviewEmpty();
  } else {
    current = episodes.find((e) => e.index === current.index);
    renderDecision();
  }
}
function visibleEpisodes() {
  const f = { todo: isTodo, approved: (e) => e.approved && !e.bad, flagged: (e) => e.bad, all: () => true }[reviewTab];
  return episodes.filter(f);
}
function renderReviewList() {
  const n = { todo: episodes.filter(isTodo).length, approved: episodes.filter((e) => e.approved && !e.bad).length,
    flagged: episodes.filter((e) => e.bad).length, all: episodes.length };
  const labels = { todo: "To do", approved: "Approved", flagged: "Flagged", all: "All" };
  $("reviewTabs").innerHTML = Object.keys(labels).map((k) => `<button data-tab="${k}" class="${reviewTab === k ? "on" : ""}">${labels[k]} ${n[k]}</button>`).join("");
  $("reviewTabs").querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { reviewTab = b.dataset.tab; renderReviewList(); }));
  const rows = visibleEpisodes();
  $("reviewList").innerHTML = rows.length ? rows.map((e) => {
    const tag = e.approved && !e.bad ? `<span class="tag approved">✓</span>` : e.bad ? `<span class="tag flagged">✕</span>`
      : e.warnings.length ? `<span class="tag warn">!</span>` : "";
    const where = e.in_log ? `${pretty(e.item)} · ${e.shelf} round ${e.round}` : "not in the episode log";
    return `<div class="item ${current && current.index === e.index ? "active" : ""}" data-i="${e.index}"><span class="name">Episode ${pad(e.index)}</span>${tag}` +
      `<span class="sub">${esc(where)} · ${e.seconds.toFixed(1)} s${e.bad && e.reason ? " · " + esc(e.reason) : ""}</span></div>`;
  }).join("") : `<p class="muted" style="padding:12px">${reviewTab === "todo" ? "Everything is reviewed." : "Nothing here."}</p>`;
  $("reviewList").querySelectorAll("[data-i]").forEach((el) => (el.onclick = () => select(Number(el.dataset.i))));
  const c = camp();
  $("reviewProgress").textContent = n.todo ? `${n.todo} left to review` : "All reviewed";
  $("reviewDoneBtn").hidden = !(c && !c.to_review && S.next && !recording());
}
$("reviewDoneBtn").onclick = () => go(S.arm_test_passed ? "record" : "check");
function showReviewEmpty(msg) {
  current = null; player.clear();
  $("reviewPlayer").hidden = true; $("reviewEmpty").hidden = false;
  $("reviewEmpty").textContent = msg || (episodes.some(isTodo) ? "Choose an episode on the left." : "Nothing left to review.");
}
async function select(index) {
  const e = episodes.find((x) => x.index === index);
  if (!e) return;
  current = e;
  $("reviewEmpty").hidden = true; $("reviewPlayer").hidden = false;
  $("epTitle").textContent = `Episode ${pad(e.index)}`;
  $("epTarget").textContent = e.in_log ? pretty(e.item) : "";
  $("epMeta").textContent = [e.in_log ? `${e.shelf}, round ${e.round} (${e.position})` : "", `${e.seconds.toFixed(1)} s`].filter(Boolean).join(" · ");
  $("epWarnings").innerHTML = e.warnings.map((w) => `<span>! ${esc(w)}</span>`).join("");
  $("reasons").hidden = true;
  renderDecision(); renderReviewList();
  await player.show(e);
}
function renderDecision() {
  if (!current) return;
  const d = $("epDecision");
  d.className = "decision " + (current.bad ? "flagged" : current.approved ? "approved" : "");
  d.textContent = !current.in_log ? "Recorded outside a plan: cannot be reviewed here."
    : current.bad ? `Flagged: ${current.reason || "bad"} (it is recorded again)` : current.approved ? "Approved" : "Not reviewed yet";
  $("approveBtn").disabled = $("flagBtn").disabled = !current.in_log;
}
async function decide(decision, reason = "") {
  if (!current || !current.in_log) return;
  try {
    await api("/api/review", { index: current.index, decision, reason });
    Object.assign(current, { approved: decision === "approve", bad: decision === "flag", reason });
    $("reasons").hidden = true;
    renderDecision(); renderReviewList();
    S = await api("/api/studio");
    renderPlanComplete(); renderSteps(); renderTop(); renderReviewList();
    const next = episodes.find((e) => isTodo(e) && e.index > current.index) || episodes.find(isTodo);
    if (next) setTimeout(() => select(next.index), 250);
    else toast(S.next ? "All episodes reviewed. Press Next round." : "All episodes reviewed.");
  } catch (e) { toast(e.message); }
}
$("approveBtn").onclick = () => decide("approve");
$("flagBtn").onclick = () => { $("reasons").hidden = !$("reasons").hidden; };
$("reasons").insertAdjacentHTML("beforeend", REASONS.map((r) => `<button data-r="${esc(r)}">${esc(r)}</button>`).join(""));
$("reasons").querySelectorAll("[data-r]").forEach((b) => (b.onclick = () => decide("flag", b.dataset.r)));
function neighbour(step) {
  const rows = visibleEpisodes();
  const i = rows.findIndex((e) => current && e.index === current.index);
  return rows[i + step] || null;
}
function renderPlanComplete() {
  const c = camp();
  const show = !!(c && !c.draft && !S.next && c.episodes);
  $("planComplete").hidden = !show;
  if (!show) return;
  const fin = S.finish;
  $("planCompleteText").innerHTML = c.to_review ? `Review the last ${c.to_review} episode(s) first.`
    : c.flagged ? `${c.approved} episodes approved. <b>${c.flagged} flagged</b> episode(s) are still in the dataset: remove them ` +
      `before training. The dataset is then renumbered, and the old copy is kept next to it as <code>${esc(c.folder)}_old</code> (the admin deletes it).`
    : `All ${c.approved} episodes are approved. The dataset is ready: <code>policy/data_collection/dataset/${esc(c.folder)}</code>.`;
  $("removeFlaggedBtn").hidden = !(c.flagged && !c.to_review) || finishing();
  $("finishStatus").hidden = !fin;
  if (fin) {
    const st = { running: ["Removing…", "warn"], finished: ["Removed", "ok"], failed: ["Failed", "bad"], stopped: ["Stopped", "warn"] }[fin.status];
    $("finishState").textContent = st[0]; $("finishState").className = "state " + st[1];
    $("finishSub").textContent = fin.status === "running" ? "" : fin.log[fin.log.length - 1] || "";
  }
  $("finishLog").hidden = !(fin && fin.status === "failed");
  if (fin) $("finishLog").textContent = fin.log.join("\n");
}
$("removeFlaggedBtn").onclick = async () => {
  if (!confirm(`Remove the ${camp().flagged} flagged episode(s) from the dataset? The episodes are renumbered.`)) return;
  try { await api("/api/remove_flagged", {}); S = await api("/api/studio"); prevFinishing = true; renderPlanComplete(); } catch (e) { toast(e.message, 6000); }
};

// ---------- 5. train ----------
const sending = () => !!(S && S.send && S.send.status === "running");
const hm = (s) => { s = Math.round(s / 60); return s >= 60 ? `${Math.floor(s / 60)} h ${String(s % 60).padStart(2, "0")} min` : `${s} min`; };
const gb = (x) => (x == null ? "?" : `${Math.round(x)} GB`);
const runsOfCampaign = () => (T && T.status.ok && camp() ? T.status.runs.filter((r) => r.dataset === camp().folder) : []);
function trainDone() { return runsOfCampaign().some((r) => r.status === "finished"); }

let trainTimer = null, runTimer = null, trainLoading = false, planTimer = null;
let stepsPlan = null;  // estimate for the steps typed in (T.plan is for the default steps)
function enterTrain() {
  clearInterval(trainTimer); clearInterval(runTimer);
  loadTrain();
  trainTimer = setInterval(() => { if (page === "train") loadTrain(); else clearInterval(trainTimer); }, 6000);
  runTimer = setInterval(() => { if (page === "train") loadRun(); else clearInterval(runTimer); }, 10000);
}
async function loadTrain() {
  if (trainLoading) return;
  trainLoading = true;
  try { T = await api("/api/train"); } catch (e) { T = null; $("trainPc").innerHTML = `<div class="note bad">${esc(e.message)}</div>`; }
  trainLoading = false;
  if (!T) return;
  if (!$("trainSteps").value) { $("trainSteps").value = T.default_steps; $("trainSteps").min = T.min_steps; $("trainSteps").max = T.max_steps; }
  renderTrain();
  renderSteps();
}
function renderTrain() {
  const st = T.status;
  if (!st.ok) {
    $("trainPc").innerHTML = `<div class="note bad">${esc(st.error)}</div><p class="muted small">${esc(st.host)}</p>`;
  } else {
    const gpus = st.gpus.map((g) => `GPU ${g.index}: ${esc(g.name)}, ${(g.used_mb / 1000).toFixed(1)} of ${(g.total_mb / 1000).toFixed(0)} GB used`).join("<br>");
    const busy = st.trainings.length ? `<div class="note warn">A training is running there.</div>` : "";
    $("trainPc").innerHTML = `${busy}<dl class="kv">${kv([["Computer", esc(st.host)], ["Project version", esc(st.commit)],
      ["Free disk", gb(st.free_gb)], ["GPUs", gpus || "none found"]])}</dl>`;
  }
  renderSend();
  const steps = Number($("trainSteps").value);
  renderPlan(T.plan && T.plan.steps === steps ? T.plan : stepsPlan && stepsPlan.steps === steps ? stepsPlan : null);
  renderRuns();
}
function renderSend() {
  if (!T) return;
  const d = T.dataset, job = S.send, now = sending();
  let html = "";
  if (d.error) html = `<div class="note warn">${esc(d.error)}</div>`;
  else {
    html = `<dl class="kv">${kv([["Dataset", `<code>${esc(d.folder)}</code>`], ["Episodes", `${d.episodes} (plan ${d.done} / ${d.total})`]])}</dl>`;
    if (d.blocker) html += `<div class="note warn">${esc(d.blocker)}</div>`;
    else if (d.done < d.total) html += `<div class="note warn">The plan is not complete yet: you can still send and train on what is recorded.</div>`;
    if (d.check_error) html += `<div class="note bad">${esc(d.check_error)}</div>`;
    else if (!now && d.same === true) html += `<div class="note ok">The training PC has this exact dataset.</div>`;
    else if (!now && d.same === false) html += `<div class="note warn">${d.on_remote ? "The training PC has an older copy: send it again." : "Not on the training PC yet."}</div>`;
    if (d.running_run) html += `<div class="note warn">A training on this dataset is running: it cannot be sent again now.</div>`;
  }
  $("trainDataset").innerHTML = html;
  $("sendBarWrap").hidden = !(now || (job && job.status === "failed"));
  if (job) $("sendBar").style.width = `${job.info.percent || 0}%`;
  $("sendMsg").textContent = now ? `Sending… ${job.info.percent || 0}%${job.info.speed ? `, ${job.info.speed}` : ""}${job.info.eta ? `, ${job.info.eta} left` : ""}`
    : job && job.folder === (camp() && camp().folder) && job.status === "failed" ? `Sending failed: ${job.log[job.log.length - 1] || "see the app log"}` : "";
  $("sendStopBtn").hidden = !now;
  $("sendBtn").hidden = now;
  $("sendBtn").disabled = !!(d.error || d.blocker || d.running_run || !T.status.ok || d.same === true || recording());
  $("sendBtn").textContent = d.on_remote && d.same === false ? "Send again" : "Send dataset";
  const startable = T.status.ok && d.same === true && !now && !T.status.trainings.length;
  $("trainStartBtn").disabled = !startable;
  $("trainMsg").textContent = !T.status.ok ? "" : now ? "Wait until the dataset is sent."
    : d.same !== true ? "Send the dataset first." : T.status.trainings.length ? "A training is running on the training PC." : "";
}
function sendEnded() {
  const job = S.send;
  toast(job.status === "finished" ? "Dataset sent." : job.status === "stopped" ? "Sending cancelled." : "Sending failed: see the Train page.", 6000);
  if (page === "train") loadTrain();
}
function renderPlan(plan) {
  if (!plan) { $("trainPlan").innerHTML = ""; return; }
  const tight = plan.free_gb != null && plan.disk_needed_gb > plan.free_gb - 2;
  $("trainPlan").innerHTML = kv([
    ["Settings", `GPU ${esc(plan.gpu)}, batch ${plan.batch_size}, memory mode ${esc(plan.memory_mode)}`],
    ["Time", `about ${hm(plan.hours * 3600)} (${plan.sec_per_step} s per step)`],
    ["Checkpoints", `every ${plan.save_freq.toLocaleString()} steps; the newest ${plan.keep_last} are kept complete` +
      (plan.keep_model_every ? `, older ones only at every ${plan.keep_model_every.toLocaleString()} steps (model only)` : ", older ones are deleted")],
    ["Disk space", `<span class="${tight ? "state bad" : ""}">up to ${gb(plan.disk_needed_gb)} of ${gb(plan.free_gb)} free</span>`],
  ]);
}
$("trainSteps").oninput = () => {
  clearTimeout(planTimer);
  planTimer = setTimeout(async () => {
    try { stepsPlan = await api("/api/train/plan", { steps: $("trainSteps").value }); renderPlan(stepsPlan); $("trainMsg").textContent = ""; }
    catch (e) { stepsPlan = null; renderPlan(null); $("trainMsg").textContent = e.message; }
  }, 400);
};
$("sendBtn").onclick = async () => {
  $("sendBtn").disabled = true;
  try { await api("/api/train/send", {}); S = await api("/api/studio"); prevSending = sending(); renderSend(); }
  catch (e) { toast(e.message, 6000); $("sendBtn").disabled = false; }
};
$("sendStopBtn").onclick = () => api("/api/train/send/stop", {}).catch((e) => toast(e.message));
$("trainStartBtn").onclick = async () => {
  const steps = Number($("trainSteps").value);
  if (!confirm(`Start training on ${camp().folder} for ${steps.toLocaleString()} steps on the training PC?`)) return;
  $("trainStartBtn").disabled = true; $("trainMsg").textContent = "Starting…";
  try {
    const r = await api("/api/train/start", { steps });
    selectedRun = r.run; toast("Training started.", 5000); $("trainMsg").textContent = "";
    await loadTrain(); loadRun();
  } catch (e) { $("trainMsg").textContent = e.message; $("trainStartBtn").disabled = false; }
};

const RUN_STATES = { running: ["Training", "warn"], finished: ["Finished", "ok"], failed: ["Failed", "bad"], stopped: ["Stopped", "warn"], unknown: ["Started by hand", ""] };
function runLine(r) {
  if (r.status === "unknown") return `${r.checkpoints.length} checkpoint(s), ${gb(r.size_gb)}`;
  const parts = [`step ${(r.step || 0).toLocaleString()} / ${(r.steps || 0).toLocaleString()}`];
  if (r.loss != null) parts.push(`loss ${r.loss.toFixed(3)}`);
  if (r.status === "running" && r.eta_s != null) parts.push(`${hm(r.eta_s)} left`);
  parts.push(gb(r.size_gb));
  return parts.join(" · ");
}
function renderRuns() {
  const st = T.status;
  if (!st.ok) { $("runList").innerHTML = ""; $("runDetail").hidden = true; return; }
  if (!st.runs.length) { $("runList").innerHTML = `<p class="muted" style="padding: 10px 12px">No training runs on the training PC yet.</p>`; return; }
  if (!selectedRun || !st.runs.some((r) => r.run === selectedRun)) {
    const mine = runsOfCampaign();
    selectedRun = (mine[0] || st.runs[0]).run;
  }
  $("runList").innerHTML = st.runs.map((r) => {
    const [label, cls] = RUN_STATES[r.status] || [r.status, ""];
    return `<div class="item ${r.run === selectedRun ? "active" : ""}" data-run="${esc(r.run)}"><span class="name">${esc(r.run)}</span>` +
      `<span class="state ${cls}" style="font-size: 14px">${label}</span><span class="sub">${esc(runLine(r))}</span></div>`;
  }).join("");
  $("runList").querySelectorAll("[data-run]").forEach((el) => (el.onclick = () => { selectedRun = el.dataset.run; renderRuns(); loadRun(); }));
  if ($("runDetail").hidden || $("runDetail").dataset.run !== selectedRun) loadRun();
}
async function loadRun() {
  if (!selectedRun || !T || !T.status.ok) return;
  const name = selectedRun;
  let r;
  try { r = await api(`/api/train/run/${encodeURIComponent(name)}`); } catch (e) { return; }
  if (name !== selectedRun) return;
  const [label, cls] = RUN_STATES[r.status] || [r.status, ""];
  $("runDetail").hidden = false; $("runDetail").dataset.run = name;
  $("runState").textContent = label; $("runState").className = "state " + cls;
  $("runSub").textContent = r.run;
  $("runBar").parentElement.hidden = r.status === "unknown";
  $("runBar").style.width = r.steps ? `${Math.min(100, (100 * (r.step || 0)) / r.steps)}%` : "0";
  const ck = r.checkpoints.length
    ? `<span class="chips">${r.checkpoints.map((c) => `<span class="chip ${c.resume ? "full" : ""}" title="${c.resume ? "model + resume state" : "model only"}">${c.step.toLocaleString()}</span>`).join("")}</span>`
    : "none yet";
  const rows = [["Dataset", `<code>${esc(r.dataset)}</code>`]];
  if (r.status !== "unknown") {
    rows.push(["Step", `${(r.step || 0).toLocaleString()} / ${(r.steps || 0).toLocaleString()}`]);
    if (r.loss != null) rows.push(["Loss", r.loss.toFixed(4)]);
    if (r.sec_per_step) rows.push(["Speed", `${r.sec_per_step} s per step`]);
    if (r.status === "running" && r.eta_s != null) rows.push(["Time left", `about ${hm(r.eta_s)}`]);
    if (r.started) rows.push(["Started", new Date(r.started * 1000).toLocaleString()]);
    rows.push(["Settings", `GPU ${esc(r.gpu)}, batch ${r.batch_size}`]);
  }
  rows.push(["Checkpoints", ck], ["Size", gb(r.size_gb)]);
  $("runKv").innerHTML = kv(rows);
  $("runChart").innerHTML = lossChart(r.losses || []);
  $("runChart").hidden = !(r.losses && r.losses.length > 1);
  $("runLog").hidden = r.status !== "failed";
  $("runLog").textContent = (r.log || []).join("\n");
  $("runStopBtn").hidden = r.status !== "running";
}
function lossChart(points) {
  if (points.length < 2) return "";
  const W = 600, H = 180, L = 44, B = 20, top = 8;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]).filter((y) => isFinite(y));
  const x0 = xs[0], x1 = xs[xs.length - 1] || 1, y0 = Math.min(...ys), y1 = Math.max(...ys);
  const X = (x) => L + ((x - x0) / (x1 - x0 || 1)) * (W - L - 6);
  const Y = (y) => top + (1 - (y - y0) / (y1 - y0 || 1)) * (H - top - B);
  const d = points.filter((p) => isFinite(p[1])).map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Loss over training steps">` +
    `<line class="ax" x1="${L}" y1="${H - B}" x2="${W}" y2="${H - B}"/><line class="ax" x1="${L}" y1="${top}" x2="${L}" y2="${H - B}"/>` +
    `<text x="${L - 4}" y="${top + 10}" text-anchor="end">${y1.toFixed(2)}</text><text x="${L - 4}" y="${H - B}" text-anchor="end">${y0.toFixed(2)}</text>` +
    `<text x="${L}" y="${H - 5}">step ${x0.toLocaleString()}</text><text x="${W - 4}" y="${H - 5}" text-anchor="end">${x1.toLocaleString()}</text>` +
    `<text x="${W - 4}" y="${top + 12}" text-anchor="end">loss</text><path class="ln" d="${d}"/></svg>`;
}
$("runStopBtn").onclick = async () => {
  if (!confirm(`Stop the training ${selectedRun}? Its checkpoints stay. It can be continued from the newest one in a terminal (resume_groot.sh).`)) return;
  try { const r = await api("/api/train/stop", { run: selectedRun }); toast(r.message || "Stopped.", 5000); await loadTrain(); loadRun(); }
  catch (e) { toast(e.message, 6000); }
};

// ---------- keys ----------
document.addEventListener("keydown", (ev) => {
  const tag = document.activeElement.tagName;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(tag) && document.activeElement.type !== "range") return;
  if (ev.ctrlKey || ev.metaKey || ev.altKey || !$("restModal").hidden) return;
  if (page === "record") {
    const cmd = { " ": "start", n: "save", N: "save", x: "discard", X: "discard" }[ev.key];
    if (!cmd) return;
    ev.preventDefault();  // Space must not also press a focused button
    if (ev.repeat) return;
    const ph = phase();
    if ((cmd === "start" && ph === "waiting") || (cmd !== "start" && ph === "recording")) command(cmd);
    return;
  }
  if (page !== "review" || !current) return;
  if (player.handleKey(ev)) return;
  const keys = {
    a: () => decide("approve"), A: () => decide("approve"),
    f: () => { $("reasons").hidden = !$("reasons").hidden; }, F: () => { $("reasons").hidden = !$("reasons").hidden; },
    ArrowUp: () => { const e = neighbour(-1); if (e) select(e.index); },
    ArrowDown: () => { const e = neighbour(1); if (e) select(e.index); },
  };
  if (keys[ev.key]) { ev.preventDefault(); keys[ev.key](); }
});

// ---------- start ----------
async function loop() {
  await refresh();
  setTimeout(loop, page === "record" && recording() ? 300 : 1500);
}
loop();
setInterval(() => { if (page === "review" && !recording()) loadEpisodes(); }, 5000);
setInterval(() => api("/api/heartbeat", {}).catch(() => {}), 10000);
api("/api/heartbeat", {}).catch(() => {});

// The page's script: the class picker, the best-maps chart, the latest-runs bar and a little motion.
// (Its own file so the site can tell browsers to run no other scripts: see _headers.)
const $ = (id) => document.getElementById(id);
const fmt = (n) => (n === null || n === undefined) ? "?" : Math.round(n).toLocaleString("en-US");
const short = (n) => n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e5 ? Math.round(n / 1e3) + "k"
  : n >= 1e4 ? (n / 1e3).toFixed(1) + "k" : fmt(n);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const LEVEL_WINDOW = 5;  // "around your level", as the server counts it
const partyName = (p) => p === "solo" ? "solo" : "in a party";
const soloOrParty = (p) => p === "solo" || p === "1" ? "solo"          // also party sizes, from the
  : p === "party" || /^[2-6]$/.test(p ?? "") ? "party" : null;         // overlay's link or older visits
const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;
const mostPlayers = (list) => [...list].sort((a, b) => b.players - a.players)[0];
const JOIN = `<a class="more" href="#overlay">Get the overlay and share your farming</a>`;
// Hours of farming: "45 min", "2.5 hours", "1,204 hours"
const hours = (h) => {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  const n = h < 10 ? Math.round(h * 10) / 10 : Math.round(h);
  return `${n.toLocaleString("en-US")} hour${n === 1 ? "" : "s"}`;
};
const SECOND_JOB = 30;   // the level paths start at (2nd job); before it, a class is one group
let classes = [];        // from the server: Warrior, Magician, Archer, Thief, each with its paths
let wantPath = null;     // the path last picked, kept while the level is under SECOND_JOB

function saved() {
  try { return JSON.parse(localStorage.getItem("ask") || "null") || {}; } catch { return {}; }
}

/** What the overlay's "Best maps" link asks for, e.g. ?level=32&party=1&job=Bandit (party: its size). */
function fromLink(q = new URLSearchParams(location.search)) {
  const job = q.get("job"), out = {};
  const level = parseInt(q.get("level"), 10);
  if (level >= 1 && level <= 250) out.level = level;
  if (soloOrParty(q.get("party"))) out.party = soloOrParty(q.get("party"));
  for (const c of classes) {
    if (c.name === job || (job === "Bowman" && c.name === "Archer")) out.job = c.name;
    for (const p of c.paths) {
      const at = p.steps.indexOf(job);
      if (at >= 0) out.job = c.name;
      if (at > 0) out.path = p.name;      // a 2nd to 4th job: that path (a 1st job is on all of them)
    }
  }
  return out;
}

function chosenClass() {
  const picked = document.querySelector('input[name="job"]:checked');
  return classes.find((c) => c.name === picked?.value);
}

const firstJob = () => parseInt($("level").value, 10) < SECOND_JOB;

/** Picks a class (the one asked for, or the one most players are on) and fills in its paths. */
function pickClass(name) {
  const c = classes.find((c) => c.name === name) || mostPlayers(classes);
  document.querySelector(`input[name="job"][value="${esc(c.name)}"]`).checked = true;
  fillPaths();
}

/** The class's paths, or, under Lv 30, its 1st job with Path greyed out. */
function fillPaths() {
  const c = chosenClass();
  if (!c) return;
  const off = firstJob();
  $("path").disabled = off;
  $("path-field").classList.toggle("off", off);
  if (off) {
    $("path").innerHTML = `<option>${esc(c.paths[0].steps[0])} (1st job)</option>`;
    $("path-hint").textContent = `Paths start at Lv ${SECOND_JOB}, with your 2nd job.`;
    return;
  }
  $("path").innerHTML = c.paths.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}${p.players
    ? ` · ${plural(p.players, "player")}` : ""}</option>`).join("");
  $("path").value = c.paths.some((p) => p.name === wantPath) ? wantPath : mostPlayers(c.paths).name;
  showSteps();
}

function showSteps() {         // e.g. "Rogue → Bandit → Chief Bandit → Shadower"
  const p = chosenClass()?.paths.find((p) => p.name === $("path").value);
  $("path-hint").textContent = p ? p.steps.map((job) => job.replace(/ \(.*\)$/, "")).join(" → ") : "";
}

async function loadOptions() {
  let data = null;
  try { data = await (await fetch("/api/options", { cache: "no-store" })).json(); } catch {}
  if (!data?.classes?.length) {
    $("results").innerHTML = `<div class="note">Couldn't load the maps. Try again in a moment.</div>`;
    return;
  }
  classes = data.classes;
  $("counts").style.display = data.runs ? "" : "none";       // no big zeros on day one
  $("c-hours").textContent = (data.hours || 0) < 10 ? String(Math.round((data.hours || 0) * 10) / 10) : fmt(data.hours);
  $("c-players").textContent = fmt(data.players || 0);
  $("c-maps").textContent = fmt(data.maps || 0);
  ["c-hours", "c-players", "c-maps"].forEach((id) => countUp($(id)));
  $("classes").innerHTML = classes.map((c) => `<label><input type="radio" name="job" value="${esc(c.name)}">` +
    `<span>${esc(c.name)}</span></label>`).join("");
  const link = fromLink(), linked = Object.keys(link).length > 0;
  const last = { ...saved(), ...link };
  if (linked) history.replaceState(null, "", location.pathname);    // a reload shows the form as left
  if (last.level) $("level").value = last.level;
  if (soloOrParty(String(last.party))) $("party").value = soloOrParty(String(last.party));
  wantPath = last.path || null;
  pickClass(last.job);
  if (!data.runs) {
    $("results-title").textContent = "The community map is just getting started";
    $("results-sub").textContent = "";
    $("results").innerHTML = `<div class="note"><h3>Be one of the first to share!</h3>
      Every 15 minutes farmed by a player using the overlay shows up here. As more players join in,
      this fills with the best maps for every class, path and level.<br>${JOIN}</div>`;
    if (linked) $("results-section").scrollIntoView({ block: "start" });
    return;
  }
  ask(null, linked);
}

async function ask(event, scroll = !!event) {
  if (event) event.preventDefault();
  const c = chosenClass();
  if (!c) return;
  const level = Math.max(1, Math.min(250, parseInt($("level").value, 10) || 30)), party = $("party").value;
  $("level").value = level;
  fillPaths();
  const job = c.name, path = level < SECOND_JOB ? null : $("path").value;
  try { localStorage.setItem("ask", JSON.stringify({ job, path: path || wantPath, level, party })); } catch {}
  const where = `${path ? `${esc(path)} (${esc(job)})` : esc(job)}, Lv ${Math.max(1, level - LEVEL_WINDOW)}–${level + LEVEL_WINDOW}, ${partyName(party)}`;
  $("results-title").textContent = "Best maps";
  $("results-sub").innerHTML = `For <b>${where}</b>.`;
  $("results").innerHTML = `<div class="note">Looking...</div>`;
  if (scroll) $("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  let data;
  try {
    const query = new URLSearchParams({ class: job, ...(path ? { path } : {}), level, party });
    data = await (await fetch(`/api/stats?${query}`, { cache: "no-store" })).json();
    if (data.error) throw new Error(data.error);
  } catch {
    $("results").innerHTML = `<div class="note">Couldn't load the maps. Try again in a moment.</div>`;
    return;
  }
  const early = data.early || [];            // (an answer from before early data: none)
  if (!data.maps.length && !early.length) {
    $("results").innerHTML = `<div class="note"><h3>No maps here yet</h3>
      A map shows up once a player around your level has shared a run there.
      Help fill it in by sharing your own farming.<br>${JOIN}</div>`;
    return;
  }
  showChart(data.maps, early, data.min_players);
}

// ---------------------------------------------------------------- the chart
// One row per map, all on one scale: a dot for every player (their typical run), the
// typical EXP/hr (gold line), what the middle half of players get (the bar), and the runs
// left out as unusual (hollow rings).

const motion = () => !!window.gsap && !matchMedia("(prefers-reduced-motion: reduce)").matches;
const compact = (n) => n >= 1e6 ? `${+(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(n >= 1e5 ? 0 : 1)}k`
  : String(Math.round(n));
let shown = null;                          // what's on the chart now, for the details on hover
let watcher = null;                        // starts each row's animation as it scrolls into view

/** A scale with round numbers that fits every map's dots and bars. (Left-out runs can be far
 * off: they're pinned to the edge with their number.) */
function scaleFor(maps) {
  const all = maps.flatMap((m) => [m.exp_hr, m.exp_hr_low, m.exp_hr_high, ...(m.dots || [])]).filter((n) => n > 0);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < hi * 0.15) { lo *= 0.85; hi *= 1.12; }
  const pad = (hi - lo) * 0.08;
  lo = Math.max(0, lo - pad); hi += pad;
  const rough = (hi - lo) / 4, p = 10 ** Math.floor(Math.log10(rough)), f = rough / p;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const ticks = Array.from({ length: Math.round((hi - lo) / step) + 1 }, (_, i) => lo + i * step);
  return { lo, hi, ticks, x: (n) => Math.max(0, Math.min(100, (n - lo) / (hi - lo) * 100)) };
}

/** "Steady" when most players get about the same, "Varies a lot" when it depends on you. */
function spread(m) {
  if (m.players < 5) return "";
  const r = (m.exp_hr_high - m.exp_hr_low) / m.exp_hr;
  return r < 0.15 ? `<span class="chip steady" title="Most players get about the same here">Steady</span>`
    : r > 0.35 ? `<span class="chip varies" title="Gear, skills and how busy the map is make a big difference here">Varies a lot</span>`
    : "";
}

function rowHtml(m, i, s, early, need) {
  const x = s.x, dots = m.dots || [], out = m.unusual || [];
  const lo = x(m.exp_hr_low), hi = x(m.exp_hr_high), mid = x(m.exp_hr);
  const band = early || m.players < 2 ? "" : `<b class="band" style="left:${lo}%;width:${Math.max(0.8, hi - lo)}%;` +
    `transform-origin:${hi > lo ? ((mid - lo) / (hi - lo)) * 100 : 50}% 50%"></b>`;
  const edge = (d) => d > s.hi ? `<span class="edge" style="right:0">${compact(d)} ›</span>`
    : d < s.lo ? `<span class="edge" style="left:0">‹ ${compact(d)}</span>` : "";
  const marks = dots.map((d) => `<i class="dot" style="left:${x(d)}%" data-v="${Number(d)}"></i>`).join("") +
    out.map((d) => `<i class="dot out" style="left:${x(d)}%" data-v="${Number(d)}"></i>${edge(d)}`).join("");
  const best = i === 0 && !early;
  return `<div class="row${best ? " best" : ""}${early ? " early" : ""}" data-at="${early ? "e" : "m"}${i}">
    <div class="rank">${early ? "" : i + 1}</div>
    <div class="info"><h3>${esc(m.map)}${best ? '<span class="tag">Best pick</span>' : ""}</h3>
      <div class="meta">${plural(m.players, "player")} · ${hours(m.hours)}<span class="wide-only"> of farming</span></div>
      ${early ? `<span class="chip wait">Needs ${plural(need, "more player")}</span>` : spread(m)}</div>
    <div class="track" aria-hidden="true"><i class="base"></i>${s.ticks.map((t) => `<i class="grid" style="left:${x(t)}%"></i>`).join("")}
      ${band}<b class="med" style="left:${mid}%"></b>${marks}</div>
    <div class="nums"><div class="exp"><span data-n="${Number(m.exp_hr)}">${fmt(m.exp_hr)}</span><small>EXP/hr</small></div>
      <div class="mesos">${m.mesos_hr === null ? "mesos/hr not known" : `${fmt(m.mesos_hr)} mesos/hr`}</div></div>
    <span class="sr-only">Typical ${fmt(m.exp_hr)} EXP/hr${m.players > 1 ? `; half of players get ${fmt(m.exp_hr_low)} to ${fmt(m.exp_hr_high)}` : ""}.</span>
  </div>`;
}

function showChart(maps, early, minPlayers) {
  const s = scaleFor([...maps, ...early]);
  shown = { maps, early };
  const touch = matchMedia("(hover: none)").matches;
  $("results").innerHTML = `<div class="chart">
    <div class="chart-top">
      <div class="legend" aria-hidden="true"><span><i class="lg-dot"></i>One player</span><span><i class="lg-med"></i>Typical</span>
        <span><i class="lg-band"></i>Middle half of players</span><span><i class="lg-out"></i>Left out (unusual)</span></div>
      <small>${touch ? "Tap" : "Point at"} a dot for details</small>
    </div>
    <div class="axis" aria-hidden="true"><div class="scale">${s.ticks.map((t) => `<b style="left:${s.x(t)}%">${compact(t)}</b>`).join("")}</div></div>
    ${maps.map((m, i) => rowHtml(m, i, s, false)).join("")}
    ${early.length ? `<div class="early-head"><b>${maps.length ? "Early data" : "Nothing ranked here yet"}</b>
      <span>${maps.length ? `Fewer than ${minPlayers} players so far, so not ranked yet.`
        : `No map has ${minPlayers} players around your level yet. Here's what's come in so far.`}</span></div>
      ${early.map((m, i) => rowHtml(m, i, s, true, minPlayers - m.players)).join("")}` : ""}
    <div class="chart-foot">EXP/hr. Each player counts once, however much they play. ${JOIN.replace('class="more" ', "")}</div>
  </div><div class="tip" role="status"><b></b><span></span></div>`;
  spreadDots();
  hookDetails();
  animateChart();
}

/** Dots close together are moved up or down a little, so each one can be seen. */
function spreadDots() {
  const lanes = [0, -7, 7, -14, 14];
  document.querySelectorAll("#results .track").forEach((track) => {
    const w = track.clientWidth, last = new Map();
    [...track.querySelectorAll(".dot")].map((el) => ({ el, at: parseFloat(el.style.left) / 100 * w }))
      .sort((a, b) => a.at - b.at).forEach(({ el, at }) => {
        const lane = lanes.find((y) => !last.has(y) || at - last.get(y) >= 10) ??
          lanes.reduce((a, b) => (last.get(a) <= last.get(b) ? a : b));
        last.set(lane, at);
        el.style.setProperty("--y", `${lane}px`);
      });
  });
}

/** Point at (or tap) a row: the nearest dot, the gold line or the bar says what it is. */
function hookDetails() {
  const box = $("results"), tip = box.querySelector(".tip");
  let lit = null;
  const hide = () => { tip.classList.remove("show"); lit?.classList.remove("on"); lit = null; };
  const say = (el, title, text, track) => {
    if (lit !== el) { lit?.classList.remove("on"); el.classList.add("on"); lit = el; }
    tip.querySelector("b").textContent = title;
    tip.querySelector("span").textContent = text;
    const wrap = box.getBoundingClientRect(), r = el.getBoundingClientRect(), t = track.getBoundingClientRect();
    const half = tip.offsetWidth / 2;
    const left = Math.max(half + 4, Math.min(wrap.width - half - 4, r.left + r.width / 2 - wrap.left));
    if (!tip.classList.contains("show")) { tip.classList.add("jump"); void tip.offsetWidth; }
    tip.style.left = `${left}px`;
    tip.style.top = `${t.top - wrap.top + 4}px`;
    tip.classList.add("show");
    requestAnimationFrame(() => tip.classList.remove("jump"));
  };
  box.querySelectorAll(".track").forEach((track) => {
    const row = track.closest(".row"), at = row.dataset.at;
    const m = (at[0] === "e" ? shown.early : shown.maps)[+at.slice(1)], early = at[0] === "e";
    const move = (e) => {
      const px = e.clientX - track.getBoundingClientRect().left, reach = e.pointerType === "touch" ? 26 : 16;
      let near = null, gap = reach;
      for (const el of track.querySelectorAll(".dot, .med")) {
        const d = Math.abs(el.offsetLeft + el.offsetWidth / 2 - px);
        if (d < gap) { near = el; gap = d; }
      }
      const band = track.querySelector(".band");
      if (!near && band && px >= band.offsetLeft && px <= band.offsetLeft + band.offsetWidth) near = band;
      if (!near) return hide();
      const v = Number(near.dataset.v);
      if (near.classList.contains("out")) {
        say(near, `${fmt(v)} EXP/hr · left out`, v > m.exp_hr
          ? "Over double what others get here: a leech, or very different gear."
          : "Under half what others get here: AFK, or not really farming.", track);
      } else if (near.classList.contains("dot")) {
        say(near, `${fmt(v)} EXP/hr`, "One player's typical run here.", track);
      } else if (near.classList.contains("med")) {
        say(near, `${fmt(m.exp_hr)} EXP/hr typical`, early ? `So far, from ${plural(m.players, "player")}.`
          : "Half of players get more, half less.", track);
      } else {
        say(near, `${short(m.exp_hr_low)} – ${short(m.exp_hr_high)} EXP/hr`, "What the middle half of players get.", track);
      }
    };
    track.addEventListener("pointermove", move);
    track.addEventListener("pointerdown", move);
    track.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") hide(); });
    track.addEventListener("pointercancel", hide);
  });
  hideDetails = hide;
}
let hideDetails = () => {};
document.addEventListener("pointerdown", (e) => { if (!e.target.closest?.("#results .track")) hideDetails(); });

/** Each row comes in as it scrolls into view: the bar grows out from the typical line,
 * the dots pop in from left to right and the number counts up. */
function animateChart() {
  watcher?.disconnect();
  if (!motion()) return;
  const box = $("results");
  gsap.from(box.querySelectorAll(".legend span, .chart-top small, .axis b"),
    { autoAlpha: 0, duration: 0.8, stagger: 0.035, ease: "power2.out", clearProps: "opacity,visibility" });
  watcher = new IntersectionObserver((entries) => {
    entries.filter((e) => e.isIntersecting).forEach((e, n) => {
      watcher.unobserve(e.target);
      gsap.delayedCall(n * 0.08, () => e.target._play.play());
    });
  }, { rootMargin: "0px 0px -6% 0px" });
  box.querySelectorAll(".row").forEach((row) => {
    const clear = "transform,opacity,visibility";
    const tl = gsap.timeline({ paused: true, defaults: { ease: "power3.out" } });
    tl.from(row, { autoAlpha: 0, y: 18, duration: 0.8, clearProps: clear });
    const band = row.querySelector(".band"), dots = row.querySelectorAll(".dot, .edge"), num = row.querySelector("[data-n]");
    if (band) tl.from(band, { scaleX: 0, duration: 1.2, ease: "expo.out", clearProps: clear }, 0.15);
    tl.from(row.querySelector(".med"), { scaleY: 0, duration: 0.7, ease: "back.out(2)", clearProps: clear }, 0.3);
    if (dots.length) {
      tl.from(dots, { scale: 0, autoAlpha: 0, duration: 0.5, ease: "back.out(2.6)", clearProps: clear,
        stagger: Math.min(0.035, 0.7 / dots.length) }, 0.35);
    }
    const end = Number(num.dataset.n), n = { v: 0 };
    tl.to(n, { v: end, duration: 1.3, ease: "power2.out", onUpdate: () => { num.textContent = fmt(n.v); } }, 0.15);
    row._play = tl;
    watcher.observe(row);
  });
}

let resizing = 0;
addEventListener("resize", () => { clearTimeout(resizing); resizing = setTimeout(spreadDots, 150); });

/** Counts a number up from 0, keeping how it's written ("2.5", "1,204"). */
function countUp(el) {
  const text = el.textContent, end = parseFloat(text.replace(/,/g, "")), decimals = (text.split(".")[1] || "").length;
  if (!motion() || !(end > 0)) return;
  const n = { v: 0 };
  el.textContent = "0";
  gsap.to(n, { v: end, duration: 1.8, delay: 0.35, ease: "power3.out",
    onUpdate: () => { el.textContent = decimals ? n.v.toFixed(decimals) : fmt(n.v); },
    onComplete: () => { el.textContent = text; } });
}

// ---------------------------------------------------------------- the bar at the top
// The latest runs players shared, one at a time (the server picks them: one per player,
// nothing unusual). Clicking one shows the best maps for that level, class and party.

let news = [], newsAt = 0, newsHeld = false;
const ago = (iso) => {
  const min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  return min < 2 ? "just now" : min < 60 ? `${min} min ago` : min < 36 * 60 ? `${Math.round(min / 60)} h ago`
    : `${Math.round(min / 1440)} days ago`;
};
const jobName = (job) => job.replace(" (Fire,Poison)", " (F/P)").replace(" (Ice,Lightning)", " (I/L)");

function newsItem(r) {
  const party = r.party > 1 ? `party of ${Number(r.party)}` : "solo";
  const level = Number(r.level), size = Number(r.party);
  return `<button type="button" data-level="${level}" data-party="${size}" data-job="${esc(r.job)}"
    title="See the best maps for Lv ${level}, ${esc(jobName(r.job))}, ${size > 1 ? "in a party" : "solo"}">` +
    `<b>Lv ${level} ${esc(jobName(r.job))}</b><span class="says"> got</span> · <span class="x"><span class="n-full">${fmt(r.exp_hr)}</span><span class="n-short">${compact(r.exp_hr)}</span> EXP/hr</span> · ` +
    `<b class="m">${esc(r.map)}</b><span class="p"> · ${party}</span> · <span class="ago">${ago(r.shared)}</span></button>`;
}

/** The next run slides up into the bar (or just appears, without motion). */
function nextNews(first = false) {
  const box = $("ticker"), old = [...box.children];
  box.insertAdjacentHTML("beforeend", newsItem(news[newsAt % news.length]));
  const el = box.lastElementChild;
  if (motion() && !first) {
    gsap.fromTo(el, { yPercent: 90, autoAlpha: 0 }, { yPercent: 0, autoAlpha: 1, duration: 0.8, ease: "expo.out" });
    gsap.to(old, { yPercent: -90, autoAlpha: 0, duration: 0.45, ease: "power2.in", onComplete: () => old.forEach((o) => o.remove()) });
  } else {
    old.forEach((o) => o.remove());
    if (motion()) gsap.from(el, { autoAlpha: 0, duration: 0.8, delay: 0.4, ease: "power2.out" });
  }
}

async function loadNews() {
  let data = null;
  try { data = await (await fetch("/api/recent", { cache: "no-store" })).json(); } catch {}
  if (!data?.runs) return;
  if (!data.runs.length) {
    if (!news.length) $("news").hidden = true;            // nothing shared yet
    return;
  }
  const first = !news.length;
  news = data.runs;
  if (first) {
    newsAt = 0;
    $("news-more").textContent = "Click one for its best maps";
    nextNews(true);
  }
}

$("ticker").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-job]");
  if (!b || !classes.length) return;
  const want = fromLink(new URLSearchParams({ level: b.dataset.level, party: b.dataset.party, job: b.dataset.job }));
  if (want.level) $("level").value = want.level;
  if (want.party) $("party").value = want.party;
  wantPath = want.path || null;
  pickClass(want.job);
  ask(null, true);
});
for (const [on, held] of [["pointerenter", true], ["pointerleave", false], ["focusin", true], ["focusout", false]]) {
  $("news").addEventListener(on, () => { newsHeld = held; });
}
setInterval(() => {                       // a new one every 5 seconds, unless you're on it
  if (news.length > 1 && !newsHeld && !document.hidden) { newsAt++; nextNews(); }
}, 5000);
// The latest runs are asked for only while the page is on screen, and at most every 5 minutes: a tab
// left open in the background must not keep calling the server all day (each call counts toward the
// free plan's daily limit).
const NEWS_EVERY = 5 * 60 * 1000;
let newsAsked = 0;
function refreshNews() {
  if (document.hidden || Date.now() - newsAsked < NEWS_EVERY) return;
  newsAsked = Date.now();
  loadNews();
}
setInterval(refreshNews, 30000);
document.addEventListener("visibilitychange", refreshNews);   // back on the tab: catch up
refreshNews();

// The hero: the headline rises into place line by line, the rest fades up.
if (motion()) {
  const clear = "transform,opacity,visibility";
  gsap.timeline({ defaults: { ease: "expo.out", duration: 1.2 } })
    .from(".hero .eyebrow", { autoAlpha: 0, y: 10, clearProps: clear })
    .from(".hero h1 .ln > span", { yPercent: 110, duration: 1.3, stagger: 0.1, clearProps: "transform" }, 0.05)
    .from(".hero p.sub, .counts", { autoAlpha: 0, y: 16, stagger: 0.1, clearProps: clear }, 0.3)
    .from(".finder", { autoAlpha: 0, y: 28, duration: 1.4, clearProps: clear }, 0.2);
}
document.documentElement.classList.remove("anim");

$("classes").addEventListener("change", (e) => pickClass(e.target.value));
$("path").addEventListener("change", () => { wantPath = $("path").value; showSteps(); });
$("level").addEventListener("input", () => { if (!isNaN(parseInt($("level").value, 10))) fillPaths(); });
$("ask").addEventListener("submit", ask);
loadOptions();

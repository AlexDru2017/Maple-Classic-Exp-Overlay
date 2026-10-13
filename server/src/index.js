// Maple Classic Exp Overlay's shared farming stats, on Cloudflare Pages with a D1 database.
//
//   POST /api/runs    the overlay sends finished 1-hour runs (only if the player agreed)
//   GET  /api/stats   best maps for a class and path, level and party size
//   GET  /api/options the classes and paths, how many players each has, and totals
//   everything else   the website, from public/ (served by Cloudflare without running this)
//
// Anyone can send anything to a public server, so nothing is taken on trust: every run is
// checked for being a plausible 1-hour run, each install and connection can only send so
// many a day, and the numbers shown are medians in which a player counts once, with runs
// far from what other players get (same map, class, level and party size) left out.

const MIN_PLAYERS = 3;              // different players needed before a map is shown
const BAND = [0.5, 2.0];            // runs outside this share of the median are outliers
const LEVEL_WINDOW = 2;             // "around your level": within this many levels
const MAX_PER_REQUEST = 50;
const MAX_PER_INSTALL_DAY = 30;     // more than a day's worth of hours can't be real
const MAX_PER_IP_DAY = 120;
const CACHE_SECONDS = 60;         // new runs show up on the site within a minute

// The four classes, each split into paths named after the job they end in (4th job). A run
// counts for the path its job is on. 1st-job runs come before the path is picked, and play
// the same whichever way the player goes, so they count for every path of their class.
// Beginners (and any job not listed here) are kept but not shown.
export const CLASSES = {
  Warrior: {
    first: ["Swordman", "Warrior"],
    paths: {
      "Hero": ["Fighter", "Crusader", "Hero"],
      "Paladin": ["Page", "White Knight", "Paladin"],
      "Dark Knight": ["Spearman", "Dragon Knight", "Dark Knight"],
    },
  },
  Magician: {
    first: ["Magician"],
    paths: {
      "Arch Mage (Fire, Poison)": ["Wizard (Fire,Poison)", "Mage (Fire,Poison)", "Arch Mage (Fire,Poison)"],
      "Arch Mage (Ice, Lightning)": ["Wizard (Ice,Lightning)", "Mage (Ice,Lightning)", "Arch Mage (Ice,Lightning)"],
      "Bishop": ["Cleric", "Priest", "Bishop"],
    },
  },
  Archer: {
    first: ["Archer", "Bowman"],
    paths: {
      "Bowmaster": ["Hunter", "Ranger", "Bowmaster"],
      "Marksman": ["Crossbowman", "Sniper", "Marksman"],
    },
  },
  Thief: {
    first: ["Rogue", "Thief"],
    paths: {
      "Night Lord": ["Assassin", "Hermit", "Night Lord"],
      "Shadower": ["Bandit", "Chief Bandit", "Shadower"],
    },
  },
};

/** The jobs whose runs count for a class and path (no path: the whole class), or null. */
export function jobsFor(name, path) {
  const c = Object.hasOwn(CLASSES, name) ? CLASSES[name] : null;
  if (!c) return null;
  if (!path) return [...c.first, ...Object.values(c.paths).flat()];
  return Object.hasOwn(c.paths, path) ? [...c.first, ...c.paths[path]] : null;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (request.method === "POST" && url.pathname === "/api/runs") return await postRuns(request, env);
      if (request.method === "GET" && url.pathname === "/api/stats") {
        return await cached(request, ctx, () => getStats(url, env));
      }
      if (request.method === "GET" && url.pathname === "/api/options") {
        return await cached(request, ctx, () => getOptions(env));
      }
      if (url.pathname.startsWith("/api/")) return json({ error: "not found" }, 404);
      return env.ASSETS.fetch(request);
    } catch (e) {
      console.error(e);
      return json({ error: "something went wrong on the server" }, 500);
    }
  },
};

// ---------------------------------------------------------------- receiving runs

function text(value, max) {
  if (typeof value !== "string") return null;
  const t = value.replace(/[^\p{L}\p{N} '().,:&\/-]/gu, " ").replace(/\s+/g, " ").trim();
  return t && t.length <= max ? t : null;
}

function inRange(value, lo, hi, integer = false) {
  return typeof value === "number" && Number.isFinite(value) && value >= lo && value <= hi &&
    (!integer || Number.isInteger(value)) ? value : null;
}

function minutes(started) {          // "2026-10-12T14:05" -> minutes since 1970 (as if UTC)
  return Date.parse(started + ":00Z") / 60000;
}

/** A plausible 1-hour run, cleaned up, or a reason it isn't. */
export function checkRun(r) {
  if (!r || typeof r !== "object") return { why: "not a run" };
  const map = text(r.map_read ?? r.map, 60);
  const job = text(r.class, 40);
  const started = typeof r.started === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(r.started)
    ? r.started : null;
  const levelStart = inRange(r.level_start, 1, 250, true);
  const levelEnd = inRange(r.level_end, 1, 250, true);
  const party = inRange(r.party, 1, 6, true);
  const seconds = inRange(r.seconds, 3590, 3720);
  const exp = inRange(r.exp, 0, 1e11);
  const mesos = inRange(r.mesos ?? 0, -1e10, 1e11);
  const mesosSeconds = inRange(r.mesos_seconds ?? 0, 0, 3720);
  if (!map || !job || !started || !minutes(started)) return { why: "missing map, class or start time" };
  if (!levelStart || !levelEnd || !party) return { why: "bad level or party size" };
  if (levelEnd < levelStart || levelEnd - levelStart > 10) return { why: "impossible level change" };
  if (!seconds) return { why: "not an hour long" };
  if (exp === null || mesos === null || mesosSeconds === null) return { why: "bad EXP or mesos" };
  const expHr = exp / seconds * 3600;
  if (expHr > 5e6) return { why: "impossible EXP/hr" };
  // Mesos/hr only when the mesos were seen for at least 10 minutes of the hour.
  const mesosHr = mesosSeconds >= 600 ? Math.max(0, mesos) / mesosSeconds * 3600 : null;
  if (mesosHr !== null && mesosHr > 5e7) return { why: "impossible mesos/hr" };
  return {
    run: {
      map, job, started, level_start: levelStart, level_end: levelEnd, party, exp, seconds,
      exp_hr: expHr, mesos, mesos_seconds: mesosSeconds, mesos_hr: mesosHr,
      version: text(r.version, 24) || "?",
    },
  };
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

async function postRuns(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "not JSON" }, 400);
  }
  const install = typeof body?.install === "string" && /^[0-9a-f]{16}$/.test(body.install) ? body.install : null;
  if (!install || !Array.isArray(body.runs)) return json({ error: "expected {install, runs: [...]}" }, 400);

  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  const ipHash = await sha256(`${request.headers.get("CF-Connecting-IP") || "?"} ${day}`);
  const counts = await env.DB.prepare(
    `SELECT (SELECT COUNT(*) FROM runs WHERE install = ?1 AND received_day = ?2) AS mine,
            (SELECT COUNT(*) FROM runs WHERE ip_hash = ?3 AND received_day = ?2) AS ip`
  ).bind(install, day, ipHash).first();
  let mine = counts.mine, fromIp = counts.ip;
  // This install's runs, to catch runs that overlap in time (one person can't farm two at once).
  const { results: earlier } = await env.DB.prepare(
    "SELECT started FROM runs WHERE install = ?1 ORDER BY started DESC LIMIT 500"
  ).bind(install).all();
  const taken = earlier.map((e) => minutes(e.started));

  const results = [];
  for (const raw of body.runs.slice(0, MAX_PER_REQUEST)) {
    const started = typeof raw?.started === "string" ? raw.started.slice(0, 16) : null;
    const { run, why } = checkRun(raw);
    if (!run) {
      results.push({ started, ok: false, why });
      continue;
    }
    const at = minutes(run.started);
    if (taken.some((t) => t === at)) {
      results.push({ started, ok: true, already: true });       // sent before: fine
      continue;
    }
    if (taken.some((t) => Math.abs(t - at) < 59)) {
      results.push({ started, ok: false, why: "overlaps another run" });
      continue;
    }
    if (mine >= MAX_PER_INSTALL_DAY || fromIp >= MAX_PER_IP_DAY) {
      results.push({ started, ok: false, why: "too many runs today", retry: true });
      continue;
    }
    await env.DB.prepare(
      `INSERT OR IGNORE INTO runs (received, received_day, install, ip_hash, started, map, job, level_start,
         level_end, party, exp, seconds, exp_hr, mesos, mesos_seconds, mesos_hr, version)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)`
    ).bind(now.toISOString(), day, install, ipHash, run.started, run.map, run.job, run.level_start,
      run.level_end, run.party, run.exp, run.seconds, run.exp_hr, run.mesos, run.mesos_seconds,
      run.mesos_hr, run.version).run();
    taken.push(at);
    mine++;
    fromIp++;
    results.push({ started, ok: true });
  }
  return json({ received: results.filter((r) => r.ok).length, results });
}

// ---------------------------------------------------------------- the numbers

export function median(values) {
  const v = [...values].sort((a, b) => a - b);
  if (!v.length) return null;
  const mid = v.length >> 1;
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

function quantile(values, q) {
  const v = [...values].sort((a, b) => a - b);
  if (!v.length) return null;
  const pos = (v.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return v[lo] + (v[hi] - v[lo]) * (pos - lo);
}

/** One number per player: the median of their runs, so nobody counts more than once. */
function perPlayer(runs, field) {
  const by = new Map();
  for (const r of runs) {
    if (r[field] === null || r[field] === undefined) continue;
    if (!by.has(r.install)) by.set(r.install, []);
    by.get(r.install).push(r[field]);
  }
  return [...by.values()].map(median);
}

/**
 * For one map: drop runs far from what players typically get there (outliers), then the
 * typical EXP/hr and mesos/hr of what's left. Null if fewer than MIN_PLAYERS players remain.
 */
export function summarize(runs) {
  const typical = median(perPlayer(runs, "exp_hr"));
  if (typical === null) return null;
  const kept = runs.filter((r) => r.exp_hr >= BAND[0] * typical && r.exp_hr <= BAND[1] * typical);
  const players = new Set(kept.map((r) => r.install)).size;
  if (players < MIN_PLAYERS) return null;
  const exp = perPlayer(kept, "exp_hr"), mesos = perPlayer(kept, "mesos_hr");
  return {
    exp_hr: Math.round(median(exp)),
    exp_hr_low: Math.round(quantile(exp, 0.25)),
    exp_hr_high: Math.round(quantile(exp, 0.75)),
    mesos_hr: mesos.length ? Math.round(median(mesos)) : null,
    players,
    runs: kept.length,
    outliers: runs.length - kept.length,
  };
}

async function getStats(url, env) {
  const name = url.searchParams.get("class"), path = url.searchParams.get("path") || null;
  const jobs = jobsFor(name, path);
  const level = parseInt(url.searchParams.get("level"), 10);
  const party = parseInt(url.searchParams.get("party") || "1", 10);
  if (!jobs || !(level >= 1 && level <= 250) || !(party >= 1 && party <= 6)) {
    return json({ error: "give class (and path), level (1-250) and party (1-6)" }, 400);
  }
  const { results: runs } = await env.DB.prepare(
    `SELECT install, map, exp_hr, mesos_hr FROM runs
     WHERE job IN (${jobs.map((_, i) => `?${i + 4}`).join(", ")}) AND party = ?1 AND level_start BETWEEN ?2 AND ?3`
  ).bind(party, level - LEVEL_WINDOW, level + LEVEL_WINDOW, ...jobs).all();
  const byMap = new Map();
  for (const r of runs) {
    if (!byMap.has(r.map)) byMap.set(r.map, []);
    byMap.get(r.map).push(r);
  }
  const maps = [];
  let waiting = 0;
  for (const [map, list] of byMap) {
    const s = summarize(list);
    if (s) maps.push({ map, ...s });
    else waiting++;
  }
  maps.sort((a, b) => b.exp_hr - a.exp_hr);
  return json({
    class: name, path, jobs, party, levels: [Math.max(1, level - LEVEL_WINDOW), level + LEVEL_WINDOW],
    min_players: MIN_PLAYERS, maps, maps_waiting_for_players: waiting,
  });
}

async function getOptions(env) {
  const { results } = await env.DB.prepare(
    "SELECT job, install, COUNT(*) AS runs FROM runs GROUP BY job, install"
  ).all();
  const count = (jobs) => {
    const want = new Set(jobs), players = new Set();
    let runs = 0;
    for (const r of results) {
      if (want.has(r.job)) {
        players.add(r.install);
        runs += r.runs;
      }
    }
    return { runs, players: players.size };
  };
  const classes = Object.entries(CLASSES).map(([name, c]) => ({
    name, ...count(jobsFor(name)),
    paths: Object.entries(c.paths).map(([path, jobs]) => ({
      name: path, steps: [c.first[0], ...jobs], ...count(jobsFor(name, path)),   // steps: 1st job to 4th
    })),
  }));
  const totals = await env.DB.prepare(
    "SELECT COUNT(*) AS runs, COUNT(DISTINCT install) AS players, COUNT(DISTINCT map) AS maps FROM runs"
  ).first();
  return json({ classes, ...totals });
}

// ---------------------------------------------------------------- helpers

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*" },
  });
}

async function cached(request, ctx, make) {
  const cache = caches.default;
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await make();
  if (response.status === 200) {
    response.headers.set("cache-control", `public, max-age=${CACHE_SECONDS}`);
    ctx.waitUntil(cache.put(request, response.clone()));
  }
  return response;
}

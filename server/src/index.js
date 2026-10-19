// Maple Classic Exp Overlay's shared farming stats, on Cloudflare Pages with a D1 database.
//
//   POST /api/runs    the overlay sends finished runs (only if the player agreed)
//   GET  /api/stats   best maps for a class and path, level, solo or in a party
//   GET  /api/options the classes and paths, how many players each has, and totals
//   GET  /api/recent  the latest runs shared, for the bar at the top of the site
//   everything else   the website, from public/ (served by Cloudflare without running this)
//
// Anyone can send anything to a public server, so nothing is taken on trust: every run is
// checked for being a plausible run (including no more EXP than its levels hold), each
// install and connection can only send so many a day (and only a few installs can share
// from one connection), and the numbers shown are medians in which a player counts once,
// with runs far from what other players get (same map, class, level, solo or party) left out.
// Posts must be JSON, so other websites can't make their visitors' browsers send runs.

const MIN_PLAYERS = 3;              // different players needed before a map is ranked
const MAX_EARLY = 8;                // maps with fewer players shown (greyed out) as early data
const LATEST = 12;                  // runs in the bar at the top of the site
const LATEST_DAYS = 14;             // how far back it looks
const BAND = [0.5, 2.0];            // runs outside this share of the median are outliers
const LEVEL_WINDOW = 5;             // "around your level": within this many levels
const MAX_PER_REQUEST = 50;
const RUN_SECONDS = [890, 3720];   // a run is 15 minutes (an hour from 0.9.3, still taken)
const MAX_PER_INSTALL_DAY = 100;    // more than a day's worth of 15-minute runs can't be real
const MAX_PER_IP_DAY = 400;
const MAX_INSTALLS_PER_IP_DAY = 6;  // copies of the overlay sharing from one connection a day (a party)
const MAX_BODY = 200_000;           // bytes in one post (50 runs are about 25,000)
const DAYS_BACK = 60;               // how old a run can be when it's sent (and up to 2 days ahead)
// How long answers are kept (see cached()): new runs show up on the site within a few minutes.
const EDGE_SECONDS = 60;            // in Cloudflare's cache, where the visitor is
const KEEP = { stats: 120, latest: 120, options: 600 };   // in the database, for everyone

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

// EXP needed to finish each level in MapleStory Classic World (index 0 = level 1): the same
// table as the overlay's (maple_exp.py), so a run can't claim more EXP than its levels hold.
const EXP_TABLE = [
  15, 34, 57, 92, 135, 372, 560, 840, 1242, 1716, 2360, 3216, 4200, 5460, 7050, 8840, 11040, 13716, 16680,
  20216, 24402, 28980, 34320, 40512, 47216, 54900, 63666, 73080, 83720, 95700, 108480, 122760, 138666,
  155540, 174216, 194832, 216600, 240500, 266682, 294216, 324240, 356916, 391160, 428280, 468450, 510420,
  555680, 604416, 655200, 709716, 748608, 789631, 832902, 878545, 926689, 977471, 1031036, 1087536,
  1147132, 1209994, 1276301, 1346242, 1420016, 1497832, 1579913, 1666492, 1757815, 1854143, 1955750,
  2062925, 2175973, 2295216, 2420993, 2553663, 2693603, 2841212, 2996910, 3161140, 3334370, 3517093,
  3709829, 3913127, 4127566, 4353756, 4592341, 4844001, 5109452, 5389449, 5684790, 11938059, 25069923,
  52646838, 110558359, 232172553, 487562361, 1023880958, 2150150011, 4515315023, 9482161548
];

/** Could a run from level `start` to `end` have gained `exp`? At most all of those levels'
 * EXP (with one level to spare, in case the level was read a moment late), and at least
 * half of the whole levels in between. Past the table's end: can't tell, so yes. */
export function expPossible(start, end, exp) {
  if (end + 1 > EXP_TABLE.length) return true;
  let most = EXP_TABLE[end], least = 0;
  for (let level = start; level <= end; level++) {
    most += EXP_TABLE[level - 1];
    if (level > start && level < end) least += EXP_TABLE[level - 1];
  }
  return exp <= most && exp >= least / 2;
}

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
      if (request.method === "GET" && url.pathname === "/api/stats") return await getStats(url, env, ctx);
      if (request.method === "GET" && url.pathname === "/api/options") {
        return await cached(url, env, ctx, "options", KEEP.options, () => getOptions(env));
      }
      // (/api/latest is what pages open before 2026-10-19 ask for; it's a static file now, see public/api/latest)
      if (request.method === "GET" && (url.pathname === "/api/recent" || url.pathname === "/api/latest")) {
        return await cached(url, env, ctx, "latest", KEEP.latest, () => getLatest(env));
      }
      if (url.pathname.startsWith("/api/")) {
        return json({ error: "not found" }, ["GET", "POST"].includes(request.method) ? 404 : 405);
      }
      return env.ASSETS.fetch(request);
    } catch (e) {
      console.error(e);
      return json({ error: "something went wrong on the server" }, 500);
    }
  },
};

// ---------------------------------------------------------------- receiving runs

// No map in the game looks like a web address, and the site shows map names, so a "map"
// like "free-mesos.com" or "www.x" is turned down.
const WEB_ADDRESS = /:\/\/|www\.|\.(com|net|org|gg|io|co|me|ly|tv|xyz|ru|cn|info|biz|link|site|shop|app|dev|to|cc|top)\b/i;

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

/** A plausible run, cleaned up, or a reason it isn't. */
export function checkRun(r, now = Date.now()) {
  if (!r || typeof r !== "object") return { why: "not a run" };
  const map = text(r.map_read ?? r.map, 60);
  const job = text(r.class, 40);
  const started = typeof r.started === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(r.started)
    ? r.started : null;
  const levelStart = inRange(r.level_start, 1, 250, true);
  const levelEnd = inRange(r.level_end, 1, 250, true);
  const party = inRange(r.party, 1, 6, true);
  const seconds = inRange(r.seconds, ...RUN_SECONDS);
  const exp = inRange(r.exp, 0, 1e11);
  const mesos = inRange(r.mesos ?? 0, -1e10, 1e11);
  const mesosSeconds = inRange(r.mesos_seconds ?? 0, 0, RUN_SECONDS[1]);
  if (!map || !job || !started || !minutes(started)) return { why: "missing map, class or start time" };
  if (WEB_ADDRESS.test(map) || WEB_ADDRESS.test(job)) return { why: "not a map name" };
  const ago = now / 60000 - minutes(started);       // (the overlay sends its local time)
  if (ago > DAYS_BACK * 1440 || ago < -2 * 1440) return { why: "start time out of range" };
  if (!levelStart || !levelEnd || !party) return { why: "bad level or party size" };
  if (levelEnd < levelStart || levelEnd - levelStart > 10) return { why: "impossible level change" };
  if (!seconds) return { why: "not a 15-minute run" };
  if (exp === null || mesos === null || mesosSeconds === null) return { why: "bad EXP or mesos" };
  const expHr = exp / seconds * 3600;
  if (expHr > 5e6 || !expPossible(levelStart, levelEnd, exp)) return { why: "impossible EXP for those levels" };
  // Mesos/hr only when the mesos were seen for at least 10 minutes of the run.
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

/** A code for a connection on one day. With the IP_SALT secret (set on the live site) it
 * can't be worked back to the internet address by trying them all. */
async function connectionCode(ip, day, salt) {
  const data = new TextEncoder().encode(`${ip} ${day}`);
  let digest;
  if (salt) {
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(salt), { name: "HMAC", hash: "SHA-256" },
      false, ["sign"]);
    digest = await crypto.subtle.sign("HMAC", key, data);
  } else {
    digest = await crypto.subtle.digest("SHA-256", data);     // (only when testing locally)
  }
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

async function postRuns(request, env) {
  if (!(request.headers.get("content-type") || "").toLowerCase().startsWith("application/json")) {
    return json({ error: "send JSON" }, 415);
  }
  if (Number(request.headers.get("content-length") || 0) > MAX_BODY) return json({ error: "too big" }, 413);
  let body;
  try {
    const raw = await request.text();
    if (raw.length > MAX_BODY) return json({ error: "too big" }, 413);
    body = JSON.parse(raw);
  } catch {
    return json({ error: "not JSON" }, 400);
  }
  const install = typeof body?.install === "string" && /^[0-9a-f]{16}$/.test(body.install) ? body.install : null;
  if (!install || !Array.isArray(body.runs)) return json({ error: "expected {install, runs: [...]}" }, 400);

  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  const ipHash = await connectionCode(request.headers.get("CF-Connecting-IP") || "?", day, env.IP_SALT);
  const counts = await env.DB.prepare(
    `SELECT (SELECT COUNT(*) FROM runs WHERE install = ?1 AND received_day = ?2) AS mine,
            (SELECT COUNT(*) FROM runs WHERE ip_hash = ?3 AND received_day = ?2) AS ip,
            (SELECT COUNT(DISTINCT install) FROM runs WHERE ip_hash = ?3 AND received_day = ?2) AS installs,
            (SELECT COUNT(*) FROM runs WHERE ip_hash = ?3 AND received_day = ?2 AND install = ?1) AS here`
  ).bind(install, day, ipHash).first();
  let mine = counts.mine, fromIp = counts.ip;
  // A connection that has already shared for a few copies of the overlay today can't add
  // more (made-up players to push a map past MIN_PLAYERS). They're sent again tomorrow.
  const crowded = counts.installs >= MAX_INSTALLS_PER_IP_DAY && !counts.here;
  // This install's runs, to catch runs that overlap in time (one person can't farm two at once).
  const { results: earlier } = await env.DB.prepare(
    "SELECT started, seconds FROM runs WHERE install = ?1 ORDER BY started DESC LIMIT 2000"
  ).bind(install).all();
  const taken = earlier.map((e) => ({ at: minutes(e.started), long: e.seconds / 60 }));
  // Two runs overlap if each starts before the other ends. Start times are to the minute, and
  // the next run starts as one ends, so a minute's leeway.
  const overlaps = (a, b) => a.at < b.at + b.long - 1 && b.at < a.at + a.long - 1;

  const results = [];
  for (const raw of body.runs.slice(0, MAX_PER_REQUEST)) {
    const started = typeof raw?.started === "string" ? raw.started.slice(0, 16) : null;
    const { run, why } = checkRun(raw, now.getTime());
    if (!run) {
      results.push({ started, ok: false, why });
      continue;
    }
    const at = { at: minutes(run.started), long: run.seconds / 60 };
    if (taken.some((t) => t.at === at.at)) {
      results.push({ started, ok: true, already: true });       // sent before: fine
      continue;
    }
    if (taken.some((t) => overlaps(t, at))) {
      results.push({ started, ok: false, why: "overlaps another run" });
      continue;
    }
    if (mine >= MAX_PER_INSTALL_DAY || fromIp >= MAX_PER_IP_DAY || crowded) {
      results.push({ started, ok: false, why: crowded ? "too many players on one connection today" : "too many runs today",
        retry: true });
      continue;
    }
    const saved = await env.DB.prepare(
      `INSERT OR IGNORE INTO runs (received, received_day, install, ip_hash, started, map, job, level_start,
         level_end, party, exp, seconds, exp_hr, mesos, mesos_seconds, mesos_hr, version)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)`
    ).bind(now.toISOString(), day, install, ipHash, run.started, run.map, run.job, run.level_start,
      run.level_end, run.party, run.exp, run.seconds, run.exp_hr, run.mesos, run.mesos_seconds,
      run.mesos_hr, run.version).run();
    if (saved.meta.changes) await addToSummaries(env, run, install);
    taken.push(at);
    mine++;
    fromIp++;
    results.push({ started, ok: true });
  }
  return json({ received: results.filter((r) => r.ok).length, results }, 200, false);
}

/** Adds a newly saved run to the small summary tables (see schema.sql), so the site can
 * count players, maps and hours without reading every run. */
function addToSummaries(env, run, install) {
  return env.DB.batch([
    env.DB.prepare("INSERT INTO map_players (map, install) VALUES (?1, ?2) ON CONFLICT DO NOTHING").bind(run.map, install),
    env.DB.prepare(`INSERT INTO job_players (job, install, runs) VALUES (?1, ?2, 1)
                    ON CONFLICT (job, install) DO UPDATE SET runs = runs + 1`).bind(run.job, install),
    env.DB.prepare(`INSERT INTO totals (id, runs, seconds) VALUES (1, 1, ?1)
                    ON CONFLICT (id) DO UPDATE SET runs = runs + 1, seconds = seconds + excluded.seconds`).bind(run.seconds),
  ]);
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

/** 3 significant figures (74,213 -> 74,200): plenty for a dot on a chart. */
const roughly = (n) => Number(n.toPrecision(3));

/**
 * For one map: drop runs far from what players typically get there (outliers), then the
 * typical EXP/hr and mesos/hr of what's left. `ranked` once MIN_PLAYERS players remain.
 * `dots` is each player's typical EXP/hr (no IDs), and `unusual` the same for the runs left
 * out, so the site can show every player and what was dropped.
 */
export function summarize(runs) {
  const typical = median(perPlayer(runs, "exp_hr"));
  if (typical === null) return null;
  const usual = (r) => r.exp_hr >= BAND[0] * typical && r.exp_hr <= BAND[1] * typical;
  const kept = runs.filter(usual), left = runs.filter((r) => !usual(r));
  const players = new Set(kept.map((r) => r.install)).size;
  const exp = perPlayer(kept, "exp_hr"), mesos = perPlayer(kept, "mesos_hr");
  const sorted = (values) => values.map(roughly).sort((a, b) => a - b);
  return {
    ranked: players >= MIN_PLAYERS,
    exp_hr: Math.round(median(exp)),
    exp_hr_low: Math.round(quantile(exp, 0.25)),
    exp_hr_high: Math.round(quantile(exp, 0.75)),
    mesos_hr: mesos.length ? Math.round(median(mesos)) : null,
    players,
    runs: kept.length,
    hours: Math.round(kept.reduce((sum, r) => sum + r.seconds, 0) / 360) / 10,
    outliers: left.length,
    dots: sorted(exp),
    unusual: sorted(perPlayer(left, "exp_hr")),
  };
}

async function getStats(url, env, ctx) {
  const name = url.searchParams.get("class"), path = url.searchParams.get("path") || null;
  const jobs = jobsFor(name, path);
  const level = parseInt(url.searchParams.get("level"), 10);
  // Solo, or in a party of any size: parties of 2 to 6 are put together so maps fill in sooner.
  const asked = url.searchParams.get("party") || "solo";
  const party = asked === "solo" || asked === "1" ? "solo" : asked === "party" || /^[2-6]$/.test(asked) ? "party" : null;
  if (!jobs || !(level >= 1 && level <= 250) || !party) {
    return json({ error: "give class (and path), level (1-250) and party (solo or party)" }, 400);
  }
  return cached(url, env, ctx, `stats/${name}/${path || ""}/${level}/${party}`, KEEP.stats,
    () => findMaps(env, name, path, jobs, level, party));
}

async function findMaps(env, name, path, jobs, level, party) {
  const { results: runs } = await env.DB.prepare(
    `SELECT install, map, seconds, exp_hr, mesos_hr FROM runs
     WHERE job IN (${jobs.map((_, i) => `?${i + 3}`).join(", ")}) AND party ${party === "solo" ? "= 1" : ">= 2"}
       AND level_start BETWEEN ?1 AND ?2`
  ).bind(level - LEVEL_WINDOW, level + LEVEL_WINDOW, ...jobs).all();
  const byMap = new Map();
  for (const r of runs) {
    if (!byMap.has(r.map)) byMap.set(r.map, []);
    byMap.get(r.map).push(r);
  }
  // Ranked maps, best first. Maps with fewer players are early data: shown, but not ranked,
  // most players first (so one made-up player can't put a map at the top), and only maps at
  // least 2 players have shared somewhere (so one person can't show a made-up map name).
  const named = byMap.size ? await sharedByTwo(env, [...byMap.keys()]) : new Set();
  const maps = [], early = [];
  for (const [map, list] of byMap) {
    const { ranked, ...s } = summarize(list);
    if (ranked) maps.push({ map, ...s });
    else if (named.has(map)) early.push({ map, ...s });
  }
  maps.sort((a, b) => b.exp_hr - a.exp_hr);
  early.sort((a, b) => b.players - a.players || b.runs - a.runs || b.exp_hr - a.exp_hr);
  return json({
    class: name, path, jobs, party, levels: [Math.max(1, level - LEVEL_WINDOW), level + LEVEL_WINDOW],
    min_players: MIN_PLAYERS, maps, early: early.slice(0, MAX_EARLY), maps_waiting_for_players: early.length,
  });
}

/** Which of these maps at least 2 different players have shared (any class, level or party). */
async function sharedByTwo(env, maps) {
  const named = new Set();
  for (let i = 0; i < maps.length; i += 90) {              // (a query takes at most 100 values)
    const part = maps.slice(i, i + 90);
    const { results } = await env.DB.prepare(
      `SELECT map FROM map_players WHERE map IN (${part.map((_, j) => `?${j + 1}`).join(", ")})
       GROUP BY map HAVING COUNT(*) >= 2`
    ).bind(...part).all();
    for (const r of results) named.add(r.map);
  }
  return named;
}

/**
 * The latest runs shared, newest first, for the bar at the top of the site. It's the front
 * page, so: one run per player (nobody fills it), only maps at least 2 players have shared
 * (a made-up map name can't be put there), only the classes the site shows, and not runs far
 * from what other players get on that map (solo or party, around that level). Rounded, and
 * nothing about who it was.
 */
async function getLatest(env) {
  const since = new Date(Date.now() - LATEST_DAYS * 864e5).toISOString().slice(0, 10);
  const shown = new Set(Object.keys(CLASSES).flatMap((name) => jobsFor(name)));
  const { results: recent } = await env.DB.prepare(
    `SELECT install, received, map, job, level_start, party, exp_hr, mesos_hr FROM runs
     WHERE received >= ?1 ORDER BY received DESC, started DESC LIMIT 200`
  ).bind(since).all();                                       // (runs are sent a few at a time)
  const named = await sharedByTwo(env, [...new Set(recent.map((r) => r.map))]);
  const picked = [], players = new Set();
  for (const r of recent) {
    if (!shown.has(r.job) || !named.has(r.map) || players.has(r.install)) continue;
    players.add(r.install);
    picked.push(r);
    if (picked.length >= LATEST * 2) break;
  }
  // What other players get on those maps, around those levels (to leave out unusual runs).
  const maps = [...new Set(picked.map((r) => r.map))];
  const levels = picked.map((r) => r.level_start);
  const { results: others } = maps.length ? await env.DB.prepare(
    `SELECT install, map, level_start, party, exp_hr FROM runs
     WHERE map IN (${maps.map((_, i) => `?${i + 3}`).join(", ")}) AND level_start BETWEEN ?1 AND ?2`
  ).bind(Math.min(...levels) - LEVEL_WINDOW, Math.max(...levels) + LEVEL_WINDOW, ...maps).all() : { results: [] };
  const usual = (r) => {
    const like = others.filter((o) => o.map === r.map && (o.party >= 2) === (r.party >= 2) &&
      Math.abs(o.level_start - r.level_start) <= LEVEL_WINDOW);
    const each = perPlayer(like, "exp_hr");
    if (each.length < MIN_PLAYERS) return true;          // too few to tell (its EXP was checked when sent)
    const typical = median(each);
    return r.exp_hr >= BAND[0] * typical && r.exp_hr <= BAND[1] * typical;
  };
  const runs = picked.filter(usual).slice(0, LATEST).map((r) => ({
    map: r.map, job: r.job, level: r.level_start, party: r.party,
    exp_hr: roughly(r.exp_hr), mesos_hr: r.mesos_hr === null ? null : roughly(r.mesos_hr),
    shared: `${r.received.slice(0, 16)}Z`,                 // to the minute
  }));
  return json({ runs });
}

async function getOptions(env) {
  const [{ results }, { results: [totals] }] = await env.DB.batch([
    env.DB.prepare("SELECT job, install, runs FROM job_players"),
    env.DB.prepare(
      `SELECT COALESCE((SELECT runs FROM totals WHERE id = 1), 0) AS runs,
              ROUND(COALESCE((SELECT seconds FROM totals WHERE id = 1), 0) / 3600.0, 1) AS hours,
              (SELECT COUNT(DISTINCT install) FROM job_players) AS players,
              (SELECT COUNT(DISTINCT map) FROM map_players) AS maps`),
  ]);
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
  return json({ classes, ...totals });
}

// ---------------------------------------------------------------- helpers

function jsonHeaders(anySite = true) {
  const headers = { "content-type": "application/json; charset=utf-8", "x-content-type-options": "nosniff" };
  if (anySite) headers["access-control-allow-origin"] = "*";      // the numbers are public
  return headers;
}

function json(data, status = 200, anySite = true) {
  return new Response(JSON.stringify(data), { status, headers: jsonHeaders(anySite) });
}

/**
 * An answer kept for a while, so the database isn't asked again for every visitor: first in
 * Cloudflare's cache where the visitor is (a minute at most), then in the database itself,
 * shared by every Cloudflare location, so each answer is worked out at most once every
 * `seconds` however many people visit, wherever they are. The key is made from the question
 * itself, so extra bits in the address can't skip the cache. (Not when testing locally,
 * unless CACHE_LOCALLY is set.)
 */
async function cached(url, env, ctx, key, seconds, make) {
  if (!env.CACHE_LOCALLY && (url.hostname === "localhost" || url.hostname === "127.0.0.1")) return make();
  const edge = caches.default;
  const id = new Request(`${url.origin}/__cache/${encodeURIComponent(key)}`);
  const hit = await edge.match(id);
  if (hit) return hit;
  const now = Date.now();
  const kept = await env.DB.prepare("SELECT body, at FROM answers WHERE key = ?1").bind(key).first();
  let body = kept && now - kept.at < seconds * 1000 ? kept.body : null;
  if (body === null) {
    const fresh = await make();
    if (fresh.status !== 200) return fresh;
    body = await fresh.text();
    ctx.waitUntil(env.DB.prepare("INSERT OR REPLACE INTO answers (key, body, at) VALUES (?1, ?2, ?3)")
      .bind(key, body, now).run());
  }
  const response = new Response(body, { headers: {
    ...jsonHeaders(), "cache-control": `public, max-age=${Math.min(EDGE_SECONDS, seconds)}` } });
  ctx.waitUntil(edge.put(id, response.clone()));
  return response;
}

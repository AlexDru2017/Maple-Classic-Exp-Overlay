-- Moderation queries for the best-maps site. Nothing here runs by itself.
--
-- HOW: paste ONE query at a time into this command (from the server folder), changing the PASTE_ID parts.
-- (Use --command with a single line: --file only returns a summary, not rows.)
--
--   node_modules\.bin\wrangler d1 execute classicexp --remote --command "<the query>"
--
-- Start with the read-only ones (SELECT). The ones that change data say CHANGES DATA.
-- An "install" is one copy of the overlay (a random id); the site never shows it to anyone.

-- 1. TODAY: runs and new installs so far today (the daily limits are 6000 runs / 300 new installs), and totals.
SELECT (SELECT runs FROM daily WHERE day = date('now')) AS runs_today, (SELECT new_installs FROM daily WHERE day = date('now')) AS new_installs_today, (SELECT COUNT(*) FROM installs) AS installs, (SELECT COUNT(*) FROM installs WHERE banned = 1) AS banned;

-- 2. MANY NEW INSTALLS ON ONE MAP in the last 3 days (a crowd of made-up players pushing a map). Real crowds happen too:
--    look at whether the map is a normal one and whether the installs share one run pattern (query 3).
SELECT r.map, COUNT(DISTINCT r.install) AS installs, MIN(i.first_seen) AS first, MAX(i.first_seen) AS last FROM runs r JOIN installs i ON i.install = r.install WHERE i.first_seen >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-3 day') GROUP BY r.map HAVING installs >= 3 ORDER BY installs DESC LIMIT 20;

-- 3. TOO STEADY: 8+ runs whose EXP/hr never moves more than 2% (real farming wobbles; a script that adds noise is steadier or rounder).
SELECT install, COUNT(*) AS runs, ROUND(AVG(exp_hr)) AS avg_exp_hr, ROUND(MIN(exp_hr)) AS lowest, ROUND(MAX(exp_hr)) AS highest FROM runs GROUP BY install HAVING runs >= 8 AND (MAX(exp_hr) - MIN(exp_hr)) < 0.02 * AVG(exp_hr) ORDER BY runs DESC LIMIT 20;

-- 4. LEVEL JUMPS: installs whose level spread over the last 2 days is bigger than 25 (a real player gains a few levels a day).
SELECT install, MIN(level_start) AS lowest, MAX(level_start) AS highest, COUNT(*) AS runs FROM runs WHERE started >= date('now', '-2 day') GROUP BY install HAVING highest - lowest > 25 ORDER BY highest - lowest DESC LIMIT 20;

-- 5. ONE MAP ONLY with 50+ runs (can be a real grinder; look, don't assume).
SELECT install, COUNT(*) AS runs, COUNT(DISTINCT map) AS maps, MIN(started) AS from_day, MAX(started) AS to_day FROM runs GROUP BY install HAVING runs >= 50 AND maps = 1 ORDER BY runs DESC LIMIT 20;

-- 6. LOOK AT ONE INSTALL: its record, then its latest runs.
SELECT * FROM installs WHERE install = 'PASTE_ID';
SELECT started, map, job, level_start, party, ROUND(exp_hr) AS exp_hr, ROUND(mesos_hr) AS mesos_hr FROM runs WHERE install = 'PASTE_ID' ORDER BY started DESC LIMIT 60;

-- 7. OTHER INSTALLS FROM THE SAME CONNECTION(S) as that install (the code changes every day, so only same-day siblings show).
SELECT DISTINCT install FROM runs WHERE ip_hash IN (SELECT ip_hash FROM runs WHERE install = 'PASTE_ID');

-- 8. BAN AN INSTALL (CHANGES DATA): its new runs are refused and its old runs stop counting at once (the answers are cached for a few minutes).
UPDATE installs SET banned = 1, note = 'why: PASTE_REASON' WHERE install = 'PASTE_ID';
--    BAN EVERY INSTALL SEEN WITH IT (CHANGES DATA; check query 7 first):
UPDATE installs SET banned = 1, note = 'same connection as PASTE_ID' WHERE install IN (SELECT install FROM runs WHERE ip_hash IN (SELECT ip_hash FROM runs WHERE install = 'PASTE_ID'));
--    THEN CLEAR THE SITE'S SAVED ANSWERS so the ban shows at once (otherwise up to 2 minutes for maps, 6 hours for the ladder) (CHANGES DATA):
DELETE FROM answers;
--    UNBAN (CHANGES DATA):
UPDATE installs SET banned = 0, note = NULL WHERE install = 'PASTE_ID';

-- 9. DELETE AN INSTALL'S RUNS FOR GOOD (CHANGES DATA). A banned install is enough to hide them; delete only to clean up.
--    After it, run schema.sql on the remote database once (it rebuilds the summary tables; the ban stays):
--      node_modules\.bin\wrangler d1 execute classicexp --remote --file schema.sql --yes
DELETE FROM runs WHERE install = 'PASTE_ID';

-- 10. MAP NAMES THE GAME'S LIST DOESN'T HAVE (event maps, new maps, misreads, made-up text) and how many players shared them.
SELECT r.map, r.first_seen, COUNT(DISTINCT p.install) AS players FROM map_review r JOIN map_players p ON p.map = r.map GROUP BY r.map ORDER BY players DESC LIMIT 50;
--     A real new map: add its name to server/tools/make_maps.py's EXTRA list and rerun it (then deploy). Junk: nothing to do,
--     it never shows (3 players on 2 days are needed). To remove its runs: DELETE FROM runs WHERE map = 'PASTE_MAP';

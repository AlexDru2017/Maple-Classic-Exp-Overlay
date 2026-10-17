-- The stats site's database. Safe to run again on the live database (wrangler d1 execute
-- classicexp --remote --file schema.sql): it only adds what's missing, and brings the small
-- summary tables at the end back in line with the runs.

-- One row per shared 15-minute run (1-hour runs from 0.9.3). Nothing that identifies a
-- player: "install" is a random id made by the overlay, and ip_hash is a code made from the
-- internet address that changes every day (only used to limit how much one connection sends).
CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  received      TEXT    NOT NULL,          -- when the server got it (UTC)
  received_day  TEXT    NOT NULL,
  install       TEXT    NOT NULL,
  ip_hash       TEXT    NOT NULL,
  started       TEXT    NOT NULL,          -- when the run started, player's local time
  map           TEXT    NOT NULL,          -- the map name as the tracker read it
  job           TEXT    NOT NULL,
  level_start   INTEGER NOT NULL,
  level_end     INTEGER NOT NULL,
  party         INTEGER NOT NULL,
  exp           REAL    NOT NULL,
  seconds       REAL    NOT NULL,
  exp_hr        REAL    NOT NULL,
  mesos         REAL,
  mesos_seconds REAL,
  mesos_hr      REAL,                       -- NULL when the mesos weren't seen long enough
  version       TEXT,
  UNIQUE (install, started)
);
CREATE INDEX IF NOT EXISTS runs_by_group    ON runs (job, party, level_start);
CREATE INDEX IF NOT EXISTS runs_by_install  ON runs (install, started);
CREATE INDEX IF NOT EXISTS runs_by_ip       ON runs (ip_hash, received_day);
CREATE INDEX IF NOT EXISTS runs_by_received ON runs (received, started);
CREATE INDEX IF NOT EXISTS runs_by_map      ON runs (map, level_start);

-- Small summary tables, kept up to date as runs come in, so the site never has to read
-- every run (the free plan allows so many rows read a day): which players have shared each
-- map, which players play each job and how many runs, and the totals.
CREATE TABLE IF NOT EXISTS map_players (
  map     TEXT NOT NULL,
  install TEXT NOT NULL,
  PRIMARY KEY (map, install)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS job_players (
  job     TEXT    NOT NULL,
  install TEXT    NOT NULL,
  runs    INTEGER NOT NULL,
  PRIMARY KEY (job, install)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS totals (
  id      INTEGER PRIMARY KEY CHECK (id = 1),
  runs    INTEGER NOT NULL,
  seconds REAL    NOT NULL
);

-- Answers the site worked out recently, shared by every Cloudflare location, so each one is
-- worked out at most once every few minutes however many people visit.
CREATE TABLE IF NOT EXISTS answers (
  key  TEXT    PRIMARY KEY,
  body TEXT    NOT NULL,
  at   INTEGER NOT NULL                     -- when, in milliseconds since 1970
) WITHOUT ROWID;

-- Bring the summary tables exactly in line with the runs: rows with no run behind them are
-- taken out, missing ones added, counts redone. (Never emptied first, so the site never sees
-- an empty table while this runs.) Does nothing on an empty database.
DELETE FROM map_players WHERE NOT EXISTS
  (SELECT 1 FROM runs r WHERE r.map = map_players.map AND r.install = map_players.install);
DELETE FROM job_players WHERE NOT EXISTS
  (SELECT 1 FROM runs r WHERE r.job = job_players.job AND r.install = job_players.install);
INSERT OR IGNORE INTO map_players (map, install) SELECT DISTINCT map, install FROM runs;
INSERT OR REPLACE INTO job_players (job, install, runs) SELECT job, install, COUNT(*) FROM runs GROUP BY job, install;
INSERT OR REPLACE INTO totals (id, runs, seconds) SELECT 1, COUNT(*), COALESCE(SUM(seconds), 0) FROM runs;
DELETE FROM answers;

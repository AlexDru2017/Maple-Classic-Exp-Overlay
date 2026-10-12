"""Tests for the tracking and logging logic (no game or screen needed).

Run from the project folder:  py -3 tests/test_logic.py
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maple_exp as m  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def new_log():
    folder = Path(tempfile.mkdtemp())
    return m.AutoLog(15, folder / "log.json", folder / "log.txt"), folder / "log.json"


def farm(log, t0, seconds, party=1, map_name="Rocky Road II", level=27, every=3, gap_at=None, gap=0):
    """100 EXP every `every` seconds (optionally with a pause); returns (end time, logs written)."""
    t, nxt, logged = t0, t0, []
    while t < t0 + seconds:
        paused = gap_at is not None and gap_at <= t - t0 < gap_at + gap
        if t >= nxt:
            if not paused:
                log.gain(t, 100)
            nxt = t + every
        entry = log.tick(t, map_name, level, party, None)
        if entry:
            logged.append(round(t - t0))
        t += 0.5
    return t, logged


# ---- the log
log, path = new_log()
_, logged = farm(log, 0, 660)
check(f"logs after 5 min of non-stop EXP, then updates at 10 min ({logged})", logged == [300, 600])
rate = log.entries[0]["exp"] / log.entries[0]["seconds"] * 3600
check(f"EXP/hr is right (120,000, got {rate:,.0f})", abs(rate - 120000) < 2000)

log, _ = new_log()
_, logged = farm(log, 0, 540, gap_at=180, gap=20)
check(f"a 20 s break restarts the 5-minute clock ({logged})", logged and 495 <= logged[0] <= 505)

log, _ = new_log()
_, logged = farm(log, 0, 330, gap_at=100, gap=10)
check(f"a 10 s break doesn't ({logged})", logged and 300 <= logged[0] <= 301)

log, _ = new_log()
t, a = farm(log, 0, 200, party=1)
t, b = farm(log, t, 400, party=4)
check("changing party size restarts the clock", not a and b and 300 <= b[0] <= 305)

log, _ = new_log()
t, _ = farm(log, 0, 310, level=27)
farm(log, t + 60, 310, level=28)
check("a new level gets its own line", len(log.entries) == 2)

a, path = new_log()
b = m.AutoLog(15, path, path.with_suffix(".txt"))
farm(a, 0, 310, party=5)
farm(b, 1000, 310, party=4)
check("two copies of the app can't erase each other's lines",
      sorted(e["party"] for e in json.loads(path.read_text())["entries"]) == [4, 5])

e = {"date": "2026-10-11", "map": "The Land of Wild Boar II", "level": 29, "party": 4,
     "exp": 51862, "seconds": 3600, "mesos": 32713, "mesos_seconds": 3600}
check("full line format", m.format_entry(e) ==
      "Oct 11th, 2026 - The Land of Wild Boar II - Lv 29 - 4ppl party - 51,862exp/hr - 32,713mesos/hr")
check("table row format", m.log_row(e)[1:] == ("The Land of Wild Boar II", 29, "4ppl", "51,862", "32,713"))

log, _ = new_log()
log.entries = [{"date": "2026-10-08", "map": "Kerning City", "level": 27, "exp": 1, "seconds": 1},
               {"date": "2026-10-08", "map": "Rocky Road II", "level": 27, "exp": 1, "seconds": 1}]
check("a misread map name is matched to the known one", log.canonical("Ketning City") == "Kerning City")
check("Rocky Road I and II stay different maps", log.canonical("Rocky Road I") == "Rocky Road I")
check("'Il' at the end is read as 'II'", m._clean_map_name("Rocky Road Il") == "Rocky Road II")
check("the level comes from the EXP table", m.level_for_req(63666) == 27 and m.level_for_req(60000) is None)

# ---- damaged files
folder = Path(tempfile.mkdtemp())
(folder / "log.json").write_text('{"entries": [{"date": "2026-10-1', encoding="utf-8")   # cut off
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
farm(log, 0, 310)
check("a damaged log is copied before anything is saved over it",
      len(list(folder.glob("exp_log.damaged-*.json"))) == 1 and len(log.entries) == 1)
folder = Path(tempfile.mkdtemp())
good = {"date": "2026-10-11", "map": "Kerning City", "level": 27, "party": 1, "exp": 100, "seconds": 300}
(folder / "log.json").write_text(json.dumps({"version": 2, "entries": [good, {"map": 5}]}), encoding="utf-8")
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
check("a damaged line is left out (with a copy kept), the rest is kept",
      log.entries == [good] and len(list(folder.glob("exp_log.damaged-*.json"))) == 1)
m.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
m.CONFIG_PATH.write_text(json.dumps({"gap_seconds": "15", "transparency": 400, "pos": "x",
                                     "log_size": [5], "mode": "region"}), encoding="utf-8")
cfg = m.load_config()
check("damaged settings fall back to the defaults",
      cfg["gap_seconds"] == 15 and cfg["transparency"] == 90 and "pos" not in cfg
      and "log_size" not in cfg and cfg["mode"] == "window")
m.CONFIG_PATH.write_text("[1, 2", encoding="utf-8")
check("an unreadable settings file gives the defaults", m.load_config()["topmost"] is True)

# ---- mesos
t = m.MesoTracker()
for v in (9820, 9820):
    t.observe(v, 0, 0, True)
check("the first count needs 3 readings that agree exactly", t.value is None)
for v in (9820, 9850, 9850):
    t.observe(v, 0, 0, True)
check("mesos picked up are counted", t.gained == 30)
t.observe(9999, 0, 0, True)
check("a one-off reading isn't counted yet", t.value == 9850)
for v in (820, 820, 820, 9880, 9880, 9880):
    t.observe(v, 0, 0, True)
check("a lost digit and its correction don't count as mesos gained", t.gained == 30 and t.value == 9880)
for v in (25000, 25000, 25000):
    t.observe(v, 0, 0, True)
check("selling loot (a big jump) doesn't count as farming", t.gained == 30)
for v in (250000,) * 7:
    t.observe(v, 0, 0, True)
check("an extra digit (10x) needs 8 readings in a row, not 3", t.value == 25000)
t = m.MesoTracker()
for v in (20000, 20000, 20000, 15001, 15001, 20000, 20000, 20040, 20040):
    t.observe(v, 0, 0, True)
check(f"a misread twice in a row and its correction cancel out (gained {t.gained})", t.gained == 40)

# ---- EXP outliers
def exp_tracker(e=47850, p=50.0):
    tr = m.ExpTracker(); tr.start()
    for _ in range(3):
        tr.observe([(e, p)], True)                       # Lv 30, 50%
    return tr

tr = exp_tracker()
for e in (86130, 86130, 47850, 47850, 47950, 47950):     # too high twice, then the real number
    tr.observe([(e, int(e / 95700 * 10000) / 100)], True)
check(f"the real number coming back after a misread isn't taken for a level-up "
      f"(gained {tr.gained:.0f}, levels {tr.levels})", tr.levels == 0 and tr.gained == 100)
tr = exp_tracker(95500, 99.79)
for e in (150, 150, 270):                                # a real level-up into Lv 31 (108,480)
    tr.observe([(e, int(e / 108480 * 10000) / 100)], True)
check(f"a real level-up counts, and the new level's EXP is known right away "
      f"(gained {tr.gained:.0f}, Lv {m.level_for_req(tr.req)})",
      tr.levels == 1 and tr.gained == 200 + 150 + 120 and tr.req == 108480)
tr = exp_tracker()
tr.observe([(0, 0.0)], True)                             # a misread 0
check("a misread 0 doesn't crash or count", tr.gained == 0)
tr = exp_tracker(95680, 99.97)
for e, p in ((32, 0.06), (32, 0.06), (32, 0.02), (32, 0.02), (152, 0.14)):   # 0.02% misread as 0.06%, twice
    tr.observe([(e, p)], True)
check(f"a level-up whose first readings are misread still counts right (gained {tr.gained:.0f}, levels {tr.levels})",
      tr.levels == 1 and tr.gained == 20 + 32 + 120)
tr = exp_tracker(76560, 80.0)
for e in (19140, 19140, 76660, 76660):                   # 80% misread as 20% twice, then the real number
    tr.observe([(e, int(e / 95700 * 10000) / 100)], True)
check(f"EXP never drops by more than a death costs: a big drop is ignored (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.levels == 0)
tr = exp_tracker()
for _ in range(10):
    tr.observe([(47850, 70.0)], False)                   # a misread that suggests another level
check("misreads can't change the level's EXP once it's known from the table", tr.req == 95700)

# ---- EXP tracking with noisy readings: misreads must never inflate the gains
random.seed(1)
reqs, worst = [54900, 63666, 73080], 0.0
for trial in range(60):
    tr = m.ExpTracker()
    tr.start()
    lvl, e, true_gain, counting = 0, random.randint(20000, 50000), 0, False
    for step in range(500):
        if random.random() < 0.3:
            g = random.choice([50, 100, 150])
            e += g
            if e >= reqs[lvl]:
                e, lvl = e - reqs[lvl], lvl + 1
            true_gain += g if counting else 0
        p = int(e / reqs[lvl] * 10000) / 100
        bad = int(str(e)[:-1] or 0) if random.random() < 0.1 else None       # a dropped digit
        ok, note = tr.observe([(bad, p)] if bad is not None else [(e, p)], bad is None)
        if note == "baseline":
            counting, true_gain = True, 0
    worst = max(worst, (tr.gained - true_gain) / max(true_gain, 1))
check(f"misread EXP never inflates the gain (worst over-count {worst:.1%})", worst <= 0.02)

# ---- reading the real EXP bar, captured at different game resolutions
import cv2  # noqa: E402
tpl = m.DigitTemplates.load()
for f in sorted((Path(__file__).parent / "frames").glob("exp_*.png")):
    lay = m.find_exp(cv2.imread(str(f)))
    got = m.read_layout(tpl, lay) if lay else None
    check(f"reads the EXP bar at {f.stem[4:]} ({got})", got == (68601, 71.68))
check("a reading that fits a level is trusted", m.fits_exp_table(68601, 71.68))
tr = m.ExpTracker()
tr.observe([(68601, 71.68)], True)
tr.observe([(68601, 71.68)], True)
check("knows the level from two clean readings, without waiting for EXP gains",
      tr.req == 95700 and m.level_for_req(tr.req) == 30)
tr = m.ExpTracker()
tr.observe([(1, 71.68)], False)
tr.observe([(1, 71.68)], False)
check("an untrusted reading doesn't set the level", tr.req is None)
check("'1 [71.68%]' (digits cut off) fits no level, so it isn't trusted", not m.fits_exp_table(1, 71.68))

# ---- pausing by itself (a fake clock, so these run instantly)
clock = [10000.0]
m.time.monotonic = lambda: clock[0]
EXP0 = 60000


def session(gap=15):
    s = m.Session(gap)
    s.map_seen("Rocky Road II")
    for _ in range(2):                                  # learns the level (Lv 30)
        s.observe([(EXP0, round(EXP0 / 95700 * 100, 2))], True, clock[0])
    return s


def gains(s, seconds, every=3, map_name=None):
    """Kills worth 100 EXP every few seconds; the clock moves in half-second steps."""
    end, nxt = clock[0] + seconds, clock[0] + every
    while clock[0] < end:
        clock[0] += 0.5
        if clock[0] >= nxt:
            nxt += every
            e = s.exp.exp + 100
            s.observe([(e, int(e / 95700 * 10000) / 100)], True, clock[0])
        s.tick(clock[0])


def idle(s, seconds):
    end = clock[0] + seconds
    while clock[0] < end:
        clock[0] += 0.5
        s.tick(clock[0])


s = session()
s.start(clock[0])
gains(s, 30)
idle(s, 40)
check(f"no EXP for 15 s pauses by itself, without counting the idle time "
      f"({s.waiting}, {s.exp.elapsed():.0f} s)", s.waiting == "idle" and abs(s.exp.elapsed() - 30) < 0.6)
gains(s, 3.5)
check(f"the next EXP gain carries on (the gain that restarts it isn't counted: {s.exp.gained})",
      s.waiting is None and s.exp.running and s.exp.gained == 1000)
gained = s.exp.gained
s.map_seen("The Swamp of Despair I")
check("leaving the map pauses by itself", s.waiting == "map" and not s.exp.running)
s.map_seen("Rocky Road II")
gains(s, 6.5)
check("back on the same map, it carries on adding up", s.exp.running and s.exp.gained == gained + 100)
s.map_seen("The Swamp of Despair I")
gains(s, 6.5)
check(f"EXP on a different map starts from zero, still knowing the level "
      f"(gained {s.exp.gained}, Lv {m.level_for_req(s.exp.req)})",
      s.map == "The Swamp of Despair I" and s.exp.gained == 100 and s.exp.req == 95700)
s.pause()
gains(s, 6.5)
check("after pressing Pause, it stays paused", not s.exp.running and s.on is False)
s = session(gap=60)
s.start(clock[0])
gains(s, 9.5)
idle(s, 40)
check("the pause time can be changed in Settings (60 s: a 40 s break doesn't pause)", s.exp.running)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

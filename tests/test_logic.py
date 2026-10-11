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

# ---- mesos
t = m.MesoTracker()
for v in (9820, 9820, 9850, 9850):
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

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

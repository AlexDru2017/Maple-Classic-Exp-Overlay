"""Tests the stats server with made-up players: normal ones, outliers, a spammer, broken and
duplicate runs. Run against a local server (npm --prefix server run dev), never the real one:

    py -3 server/test_server.py http://localhost:8787
"""
import json
import random
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787"
assert "localhost" in BASE or "127.0.0.1" in BASE, "only run this against a local test server"
rnd = random.Random(3)
failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"status": e.code, **json.loads(e.read() or b"{}")}


def install():
    return "".join(rnd.choice("0123456789abcdef") for _ in range(16))


def run(started, exp_hr, level=31, map_name="The Land of Wild Boar II", job="Bandit", party=1, mesos_hr=30000,
        seconds=900.4):
    """A run as the overlay sends it: 15 minutes (0.9.3 sent 1-hour runs: seconds=3600.4)."""
    return {"date": started[:10], "started": started, "map": map_name, "map_read": map_name, "class": job,
            "level_start": level, "level_end": level, "party": party, "exp": exp_hr * seconds / 3600,
            "seconds": seconds, "mesos": mesos_hr * seconds / 3600, "mesos_seconds": seconds,
            "version": "0.9.4-beta", "seal": "x" * 24}


def hours(day, n, start_hour=10):
    return [f"{day}T{start_hour + i:02d}:00" for i in range(n)]


# six ordinary Bandits around Lv 31, solo, on Wild Boar Land II
normal = []
for i in range(6):
    who = install()
    typical = rnd.uniform(45000, 55000)
    r = call("/api/runs", {"install": who, "runs": [run(t, typical * rnd.uniform(0.93, 1.07), level=30 + i % 4)
                                                     for t in hours("2026-10-01", 3)]})
    normal.append(typical)
    check(f"normal player {i + 1}: all 3 runs received", r.get("received") == 3)

# a faker and an AFK player
r = call("/api/runs", {"install": install(), "runs": [run(t, 500000) for t in hours("2026-10-01", 3)]})
check("a faker's runs are received (but should be left out of the numbers)", r.get("received") == 3)
call("/api/runs", {"install": install(), "runs": [run(t, 5000) for t in hours("2026-10-01", 3)]})

# a map only two players have farmed
for _ in range(2):
    call("/api/runs", {"install": install(), "runs": [run("2026-10-01T09:00", 40000, map_name="Rocky Road II")]})

# broken runs
bad = [dict(run("2026-10-02T10:00", 50000), seconds=300),                 # only 5 minutes
       dict(run("2026-10-02T11:00", 50000), level_start=35, level_end=31),  # level going down
       dict(run("2026-10-02T12:00", 50000), **{"class": ""}),               # no class
       dict(run("2026-10-02T13:00", 50000), exp=9e9),                       # impossible EXP
       "not a run at all"]
r = call("/api/runs", {"install": install(), "runs": bad})
check(f"broken runs are refused ({[x.get('why') for x in r['results']]})", r.get("received") == 0)
check("a request without a proper install id is refused", call("/api/runs", {"install": "me", "runs": []}).get("status") == 400)

# a run sent twice, runs one after another, and runs that overlap in time
who = install()
first = run("2026-10-03T10:00", 50000)
call("/api/runs", {"install": who, "runs": [first]})
r = call("/api/runs", {"install": who, "runs": [first, run("2026-10-03T10:10", 50000)]})
check("sending the same run again is fine (not counted twice)", r["results"][0].get("already") is True)
check("a run overlapping another one is refused", r["results"][1].get("why") == "overlaps another run")
r = call("/api/runs", {"install": who, "runs": [run("2026-10-03T10:15", 50000), run("2026-10-03T10:30", 50000)]})
check(f"15-minute runs one after another are all taken ({r.get('received')} of 2)", r.get("received") == 2)
old = install()
r = call("/api/runs", {"install": old, "runs": [dict(run("2026-10-03T12:00", 50000, seconds=3600.4, job="Hunter",
                                                         map_name="Ant Tunnel I"), version="0.9.3-beta")]})
check("a 1-hour run from 0.9.3 is still taken", r.get("received") == 1)
r = call("/api/runs", {"install": old, "runs": [run("2026-10-03T12:45", 50000, job="Hunter", map_name="Ant Tunnel I")]})
check("a run inside that hour overlaps it", r["results"][0].get("why") == "overlaps another run")

# a spammer sends 150 runs (50 at a time)
spammer, received = install(), 0
for part in range(3):
    spam = [run(f"2026-10-{4 + i // 24:02d}T{i % 24:02d}:00", 50000) for i in range(part * 50, part * 50 + 50)]
    received += call("/api/runs", {"install": spammer, "runs": spam}).get("received", 0)
check(f"one install can only send so many a day ({received} of 150 received)", received == 100)

# other jobs on the same map: a Chief Bandit (the same path), Rogues (1st job: every Thief
# path), Hermits (the other Thief path), Beginners and Hunters
def players(n, job, exp_hr, day):
    for _ in range(n):
        call("/api/runs", {"install": install(), "runs": [run(f"{day}T10:00", exp_hr, job=job)]})


players(1, "Chief Bandit", 50000, "2026-10-05")
players(3, "Rogue", 50000, "2026-10-05")
players(3, "Hermit", 70000, "2026-10-05")
players(3, "Beginner", 50000, "2026-10-05")
players(3, "Hunter", 50000, "2026-10-05")
# three Bandits in parties of 2, 4 and 6: shown together under "party", never under "solo"
for size in (2, 4, 6):
    call("/api/runs", {"install": install(), "runs": [run("2026-10-06T10:00", 120000, map_name="Sleepywood Dungeon",
                                                          party=size)]})

# what the site shows
s = call("/api/stats?class=Thief&path=Shadower&level=32&party=1")
maps = {m["map"]: m for m in s["maps"]}
boar = maps.get("The Land of Wild Boar II")
print("stats:", json.dumps(s, indent=1)[:900])
check("a map farmed by enough players is shown", boar is not None)
if boar:
    check(f"the faker and the AFK player are left out ({boar['outliers']} outliers)", boar["outliers"] == 6)
    check(f"EXP/hr is the typical player's (median {boar['exp_hr']:,}, normal players 45k-55k)",
          45000 <= boar["exp_hr"] <= 55000)
    # 8 Bandits (not the faker or the AFK one), the Chief Bandit and 3 Rogues. Not the
    # Hermits, Beginners or Hunters.
    check(f"Shadower counts Bandits, Chief Bandits and Rogues, each once (players: {boar['players']})",
          boar["players"] == 12)
if boar:
    check(f"how much farming it's from, in hours ({boar['hours']} h from {boar['runs']} runs)",
          abs(boar["hours"] - boar["runs"] * 900.4 / 3600) < 0.2)
check("a map with only 2 players isn't shown yet", "Rocky Road II" not in maps and s["maps_waiting_for_players"] >= 1)
nl = call("/api/stats?class=Thief&path=Night%20Lord&level=32&party=1")
nl_boar = {m["map"]: m for m in nl["maps"]}.get("The Land of Wild Boar II")
check(f"Night Lord counts the Hermits and the Rogues, not the Bandits ({nl_boar and nl_boar['players']} players)",
      nl_boar is not None and nl_boar["players"] == 6)
everyone = {m["map"]: m for m in call("/api/stats?class=Thief&level=32&party=1")["maps"]}
check(f"no path: the whole class ({everyone['The Land of Wild Boar II']['players']} players)",
      everyone["The Land of Wild Boar II"]["players"] == 15)
check("other classes see nothing", call("/api/stats?class=Archer&path=Marksman&level=32&party=1")["maps"] == [])
check("an Archer path sees the Hunters", len(call("/api/stats?class=Archer&path=Bowmaster&level=32&party=1")["maps"]) == 1)
party = {m["map"]: m for m in call("/api/stats?class=Thief&path=Shadower&level=32&party=party")["maps"]}
check(f"parties of 2, 4 and 6 are put together ({party.get('Sleepywood Dungeon', {}).get('players')} players)",
      party.get("Sleepywood Dungeon", {}).get("players") == 3 and "The Land of Wild Boar II" not in party)
check("a party size from the overlay's link means a party",
      [m["map"] for m in call("/api/stats?class=Thief&path=Shadower&level=32&party=4")["maps"]] == list(party))
check("party runs aren't in solo", "Sleepywood Dungeon" not in maps)
check("a party of 7 is refused", call("/api/stats?class=Thief&level=32&party=7").get("status") == 400)
check(f"around your level means 5 levels either way ({s['levels']})", s["levels"] == [27, 37])
check("runs 6+ levels away are left out", call("/api/stats?class=Thief&path=Shadower&level=39&party=solo")["maps"] == [])
check("level is required", call("/api/stats?class=Thief&path=Shadower").get("status") == 400)
check("an old-style job name isn't a class", call("/api/stats?class=Bandit&level=32&party=1").get("status") == 400)
check("a path from another class is refused", call("/api/stats?class=Thief&path=Bishop&level=32").get("status") == 400)
check("odd names don't break it", call("/api/stats?class=constructor&level=32").get("status") == 400
      and call("/api/stats?class=Thief&path=__proto__&level=32").get("status") == 400)
o = call("/api/options?fresh=1")             # (answers are kept for a minute)
check(f"the totals count hours, not runs ({o['hours']} h from {o['runs']} runs)",
      0 < o["hours"] < o["runs"] and abs(o["hours"] - (o["runs"] - 1) * 900.4 / 3600 - 1) < 1)
names = [c["name"] for c in o["classes"]]
check(f"options list the 4 classes, no Beginner ({names})", names == ["Warrior", "Magician", "Archer", "Thief"])
paths = {p["name"]: p for p in o["classes"][3]["paths"]}
check(f"Thief's paths are named after the 4th job ({list(paths)})", list(paths) == ["Night Lord", "Shadower"])
check(f"players per path ({paths['Night Lord']['players']} Night Lord, {paths['Shadower']['players']} Shadower)",
      paths["Night Lord"]["players"] == 6 and paths["Shadower"]["players"] == 19)   # every Bandit (solo or not), Chief Bandit and Rogue
check(f"each path lists its jobs, 1st to 4th ({paths['Shadower']['steps']})",
      paths["Shadower"]["steps"] == ["Rogue", "Bandit", "Chief Bandit", "Shadower"])

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

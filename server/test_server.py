"""Tests the stats server with made-up players: normal ones, outliers, a spammer, broken and
duplicate runs. Run against a local server (npm --prefix server run dev), never the real one:

    py -3 server/test_server.py http://localhost:8787
"""
import hashlib
import json
import random
import sys
import time
import urllib.request
from datetime import date, timedelta

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787"
assert "localhost" in BASE or "127.0.0.1" in BASE, "only run this against a local test server"
rnd = random.Random(3)
failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def call(path, body=None, ip=None, content_type="application/json", later=None):
    """A request to the test server. Each made-up player sends from their own made-up internet
    address (the live site gets the real one from Cloudflare), unless `ip` says otherwise."""
    headers = {"content-type": content_type}
    if body is None:                                   # a page asking: "a day and an hour later", so the players
        headers["X-Test-Now"] = str(int((time.time() + 25 * 3600) * 1000))      # sent above count (see VET_HOURS)
    if later is not None:
        headers["X-Test-Now"] = str(int((time.time() + later * 3600) * 1000))
    if isinstance(body, dict) and isinstance(body.get("install"), str):
        n = int(hashlib.md5(body["install"].encode()).hexdigest()[:4], 16)
        headers["CF-Connecting-IP"] = ip or f"10.0.{n >> 8}.{n & 255}"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers)
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


def day(n):
    """Dates in the past 30 days (runs older than 60 days aren't taken): day(1) is 29 days ago."""
    return (date.today() - timedelta(days=30 - n)).isoformat()


def hours(day, n, start_hour=10):
    return [f"{day}T{start_hour + i:02d}:00" for i in range(n)]


# six ordinary Bandits around Lv 31, solo, on Wild Boar Land II
normal = []
for i in range(6):
    who = install()
    typical = rnd.uniform(45000, 55000)
    r = call("/api/runs", {"install": who, "runs": [run(t, typical * rnd.uniform(0.93, 1.07), level=30 + i % 4)
                                                     for t in hours(f"{day(1)}", 4)]})
    normal.append(typical)
    check(f"normal player {i + 1}: all 4 runs received", r.get("received") == 4)

# a faker and an AFK player
r = call("/api/runs", {"install": install(), "runs": [run(t, 250000) for t in hours(f"{day(1)}", 4)]})
check("a faker's runs are received (but should be left out of the numbers)", r.get("received") == 4)
r = call("/api/runs", {"install": install(), "runs": [run(t, 500000) for t in hours(f"{day(1)}", 4)]})
check(f"...but a run gaining 4 levels' worth an hour at Lv 31 is refused outright ({r['results'][0].get('why')})",
      r.get("received") == 0 and r["results"][0].get("why") == "impossible EXP for that level")
call("/api/runs", {"install": install(), "runs": [run(t, 5000) for t in hours(f"{day(1)}", 4)]})

# a map only two players have farmed
for _ in range(2):
    call("/api/runs", {"install": install(), "runs": [run(t, 40000, map_name="Rocky Road II") for t in hours(f"{day(1)}", 4, 4)]})

# broken runs
bad = [dict(run(f"{day(2)}T10:00", 50000), seconds=300),                 # only 5 minutes
       dict(run(f"{day(2)}T11:00", 50000), level_start=35, level_end=31),  # level going down
       dict(run(f"{day(2)}T12:00", 50000), **{"class": ""}),               # no class
       dict(run(f"{day(2)}T13:00", 50000), exp=9e9),                       # impossible EXP
       "not a run at all"]
bad += [dict(run(f"{day(2)}T14:00", 50000), exp=400000),                   # more than Lv 31 and 32 hold
        run("2020-01-01T10:00", 50000),                                     # years ago
        run(f"{(date.today() + timedelta(days=5)).isoformat()}T10:00", 50000)]  # days ahead
r = call("/api/runs", {"install": install(), "runs": bad})
check(f"broken runs are refused ({[x.get('why') for x in r['results']]})", r.get("received") == 0)
why = [x.get("why") for x in r["results"]]
check(f"more EXP than the levels hold is refused ({why[5]})", why[5] == "impossible EXP for those levels")
check(f"runs from long ago, or from days ahead, are refused ({why[6]}, {why[7]})",
      why[6] == why[7] == "start time out of range")
check("a post that isn't JSON is refused (other websites can't make browsers send runs)",
      call("/api/runs", {"install": install(), "runs": [run(f"{day(2)}T15:00", 50000)]},
           content_type="text/plain").get("status") == 415)
check("other kinds of request are refused", call("/api/runs", None).get("status") == 404)
crowd = [install() for _ in range(7)]
got = [call("/api/runs", {"install": who, "runs": [run(f"{day(3)}T08:00", 50000, map_name="Ant Tunnel II", job="Beginner")]},
            ip="192.0.2.7")["results"][0] for who in crowd]
check(f"only 6 players can share from one connection a day; the 7th is asked to try later "
      f"({[g.get('ok') for g in got]}, {got[-1].get('why')})",
      all(g["ok"] for g in got[:6]) and not got[6]["ok"] and got[6].get("retry") is True)
check("a request without a proper install id is refused", call("/api/runs", {"install": "me", "runs": []}).get("status") == 400)

# a run sent twice, runs one after another, and runs that overlap in time
who = install()
first = run(f"{day(3)}T10:00", 50000)
call("/api/runs", {"install": who, "runs": [first]})
r = call("/api/runs", {"install": who, "runs": [first, run(f"{day(3)}T10:10", 50000)]})
check("sending the same run again is fine (not counted twice)", r["results"][0].get("already") is True)
check("a run overlapping another one is refused", r["results"][1].get("why") == "overlaps another run")
r = call("/api/runs", {"install": who, "runs": [run(f"{day(3)}T10:15", 50000), run(f"{day(3)}T10:30", 50000)]})
check(f"15-minute runs one after another are all taken ({r.get('received')} of 2)", r.get("received") == 2)
old = install()
r = call("/api/runs", {"install": old, "runs": [dict(run(f"{day(3)}T12:00", 50000, seconds=3600.4, job="Hunter",
                                                         map_name="Ant Tunnel I"), version="0.9.3-beta")]})
check("a 1-hour run from 0.9.3 is still taken", r.get("received") == 1)
r = call("/api/runs", {"install": old, "runs": [run(f"{day(3)}T12:45", 50000, job="Hunter", map_name="Ant Tunnel I")]})
check("a run inside that hour overlaps it", r["results"][0].get("why") == "overlaps another run")

# a spammer sends 150 runs (50 at a time)
spammer, received = install(), 0
for part in range(3):
    spam = [run(f"{day(4 + i // 24)}T{i % 24:02d}:00", 50000) for i in range(part * 50, part * 50 + 50)]
    received += call("/api/runs", {"install": spammer, "runs": spam}).get("received", 0)
check(f"one install can only send so many a day ({received} of 150 received)", received == 70)

# other jobs on the same map: a Chief Bandit (the same path), Rogues (1st job: every Thief
# path), Hermits (the other Thief path), Beginners and Hunters
def players(n, job, exp_hr, day):
    for _ in range(n):
        call("/api/runs", {"install": install(), "runs": [run(t, exp_hr, job=job) for t in hours(day, 4)]})


players(1, "Chief Bandit", 50000, f"{day(5)}")
players(3, "Rogue", 50000, f"{day(5)}")
players(3, "Hermit", 70000, f"{day(5)}")
players(3, "Beginner", 50000, f"{day(5)}")
players(3, "Hunter", 50000, f"{day(5)}")
# three Bandits in parties of 2, 4 and 6: shown together under "party", never under "solo"
for size in (2, 4, 6):
    call("/api/runs", {"install": install(), "runs": [run(t, 120000, map_name="Cold Field I", party=size)
                                                      for t in hours(f"{day(6)}", 4)]})

# made-up map names: web addresses are turned down; a name only one player has sent isn't shown
r = call("/api/runs", {"install": install(), "runs": [run(f"{day(6)}T12:{m}", 50000, map_name=name, job="Hunter")
                                                      for m, name in (("00", "free-mesos.com"), ("15", "www.mesos"),
                                                                      ("30", "Ant Tunnel Park"))]})
why = [x.get("why") for x in r["results"]]
check(f"a map name that's a web address is turned down ({why})",
      why[:2] == ["not a map name"] * 2 and r["results"][2].get("ok"))
call("/api/runs", {"install": install(), "runs": [run(f"{day(6)}T13:00", 50000, map_name="Totally Real Map", job="Hunter")]})

# what the site shows
s = call("/api/stats?class=Thief&path=Shadower&level=32&party=1")
maps = {m["map"]: m for m in s["maps"]}
boar = maps.get("The Land of Wild Boar II")
print("stats:", json.dumps(s, indent=1)[:900])
check("a map farmed by enough players is shown", boar is not None)
if boar:
    check(f"the faker and the AFK player are left out ({boar['outliers']} outliers)", boar["outliers"] == 8)
    check(f"EXP/hr is the typical player's (median {boar['exp_hr']:,}, normal players 45k-55k)",
          45000 <= boar["exp_hr"] <= 55000)
    # 8 Bandits (not the faker or the AFK one), the Chief Bandit and 3 Rogues. Not the
    # Hermits, Beginners or Hunters.
    check(f"Shadower counts Bandits, Chief Bandits and Rogues, each once (players: {boar['players']})",
          boar["players"] == 11)
if boar:
    check(f"how much farming it's from, in hours ({boar['hours']} h from {boar['runs']} runs)",
          abs(boar["hours"] - boar["runs"] * 900.4 / 3600) < 0.2)
    dots = boar.get("dots", [])
    check(f"one dot per player, in order, inside the typical range's band ({len(dots)} dots)",
          len(dots) == boar["players"] and dots == sorted(dots)
          and all(0.5 * boar["exp_hr"] <= d <= 2 * boar["exp_hr"] for d in dots))
    check(f"the faker and the AFK player are the left-out dots ({boar.get('unusual')})",
          sorted(boar.get("unusual", [])) == [5000, 250000])
    check(f"every dot has the level its player was at ({boar.get('dot_levels')})",
          len(boar.get("dot_levels", [])) == len(dots) and all(isinstance(v, int) and 25 <= v <= 40 for v in boar["dot_levels"])
          and len(boar.get("unusual_levels", [])) == len(boar.get("unusual", [])))
    check("dots are rounded and carry no player IDs",
          all(isinstance(d, (int, float)) and d == float(f"{d:.3g}") for d in dots + boar.get("unusual", [])))
early = {m["map"]: m for m in s.get("early", [])}
check("a map with only 2 players isn't ranked yet", "Rocky Road II" not in maps and s["maps_waiting_for_players"] >= 1)
check(f"...but it's shown as early data ({early.get('Rocky Road II')})",
      early.get("Rocky Road II", {}).get("players") == 2 and len(early["Rocky Road II"].get("dots", [])) == 2
      and "ranked" not in early["Rocky Road II"])
bow = call("/api/stats?class=Archer&path=Bowmaster&level=32&party=1")
bow_early = [m["map"] for m in bow.get("early", [])]
check(f"...but not a map name only one player has sent ({bow_early})",
      "Totally Real Map" not in bow_early and "Ant Tunnel Park" not in bow_early
      and bow["maps_waiting_for_players"] == len(bow_early))
nl = call("/api/stats?class=Thief&path=Night%20Lord&level=32&party=1")
nl_boar = {m["map"]: m for m in nl["maps"]}.get("The Land of Wild Boar II")
check(f"Night Lord counts the Hermits and the Rogues, not the Bandits ({nl_boar and nl_boar['players']} players)",
      nl_boar is not None and nl_boar["players"] == 6)
everyone = {m["map"]: m for m in call("/api/stats?class=Thief&level=32&party=1")["maps"]}
check(f"no path: the whole class ({everyone['The Land of Wild Boar II']['players']} players)",
      everyone["The Land of Wild Boar II"]["players"] == 14)
check("other classes see nothing", call("/api/stats?class=Archer&path=Marksman&level=32&party=1")["maps"] == [])
check("an Archer path sees the Hunters", len(call("/api/stats?class=Archer&path=Bowmaster&level=32&party=1")["maps"]) == 1)
party = {m["map"]: m for m in call("/api/stats?class=Thief&path=Shadower&level=32&party=party")["maps"]}
check(f"parties of 2, 4 and 6 are put together ({party.get('Cold Field I', {}).get('players')} players)",
      party.get("Cold Field I", {}).get("players") == 3 and "The Land of Wild Boar II" not in party)
check("a party size from the overlay's link means a party",
      [m["map"] for m in call("/api/stats?class=Thief&path=Shadower&level=32&party=4")["maps"]] == list(party))
check("party runs aren't in solo", "Cold Field I" not in maps)
check("a party of 7 is refused", call("/api/stats?class=Thief&level=32&party=7").get("status") == 400)
check(f"around your level starts at 3 levels either way, and says how wide it can go ({s['levels']}, {s['ranges']})",
      s["levels"] == [29, 35] and s["ranges"] == [3, 5, 8])
check("runs more than 8 levels away are left out", call("/api/stats?class=Thief&path=Shadower&level=43&party=solo")["maps"] == [])
check("level is required", call("/api/stats?class=Thief&path=Shadower").get("status") == 400)
check("an old-style job name isn't a class", call("/api/stats?class=Bandit&level=32&party=1").get("status") == 400)
check("a path from another class is refused", call("/api/stats?class=Thief&path=Bishop&level=32").get("status") == 400)
check("odd names don't break it", call("/api/stats?class=constructor&level=32").get("status") == 400
      and call("/api/stats?class=Thief&path=__proto__&level=32").get("status") == 400)
# a note field that arrives anyway (the overlay never sends one) is ignored, and never shown anywhere
sneaky = install()
call("/api/runs", {"install": sneaky, "runs": [dict(run(f"{day(28)}T10:00", 52000, job="Hunter"), note="PRIVATE-NOTE-TEXT",
                                                    notes=["PRIVATE-NOTE-TEXT"])]})
seen = json.dumps([call("/api/recent"), call("/api/options?fresh=1"),
                   call("/api/stats?class=Archer&path=Bowmaster&level=32&party=1"),
                   call("/api/stats?class=Thief&path=Shadower&level=32&party=1")])
check("a note sent with a run is dropped (not stored or shown anywhere)", "PRIVATE-NOTE-TEXT" not in seen)

# the bar at the top of the site: the latest runs shared
call("/api/runs", {"install": install(), "runs": [run(f"{day(29)}T10:00", 52000, map_name="Visit my-site.com", job="Hunter")]})
call("/api/runs", {"install": install(), "runs": [run(f"{day(29)}T10:00", 400000, job="Hunter")]})        # a faker on Wild Boar
call("/api/runs", {"install": install(), "runs": [run(f"{day(29)}T10:00", 50000, job="Beginner")]})
fresh = install()
call("/api/runs", {"install": fresh, "runs": [run(f"{day(29)}T09:00", 48000, job="Hunter"), run(f"{day(29)}T09:15", 48000, job="Hunter"),
                                     run(f"{day(29)}T09:30", 48000, job="Hunter"),
                                     run(f"{day(29)}T09:45", 51000, level=32, job="Hunter")]})
latest = call("/api/recent").get("runs", [])
check(f"the bar shows the latest runs, newest first ({len(latest)} runs)",
      0 < len(latest) <= 12 and [r["shared"] for r in latest] == sorted((r["shared"] for r in latest), reverse=True))
check(f"...starting with the newest ordinary one ({latest[0] if latest else None})",
      bool(latest) and latest[0]["map"] == "The Land of Wild Boar II" and latest[0]["exp_hr"] == 51000
      and latest[0]["level"] == 32 and latest[0]["job"] == "Hunter")
check("...once per player (not both of their runs)", sum(r["exp_hr"] in (48000, 51000) for r in latest[:3]) == 1)
check("...not a map only one player has shared (made-up names stay off the front page)",
      all(r["map"] != "Visit my-site.com" for r in latest))
check("...not a run far from what others get there", all(r["exp_hr"] < 300000 for r in latest))
check("...not jobs the site doesn't show (Beginners)", all(r["job"] != "Beginner" for r in latest))
check("...and nothing about who it was", all(set(r) == {"map", "job", "level", "party", "exp_hr", "mesos_hr", "shared"}
                                               for r in latest))
o = call("/api/options?fresh=1")             # (answers are kept for a minute)
check(f"the totals count hours, not runs ({o['hours']} h from {o['runs']} runs)",
      0 < o["hours"] < o["runs"] and abs(o["hours"] - (o["runs"] - 1) * 900.4 / 3600 - 1) < 1)
names = [c["name"] for c in o["classes"]]
check(f"options list the 4 classes, no Beginner ({names})", names == ["Warrior", "Magician", "Archer", "Thief"])
paths = {p["name"]: p for p in o["classes"][3]["paths"]}
check(f"Thief's paths are named after the 4th job ({list(paths)})", list(paths) == ["Night Lord", "Shadower"])
check(f"players per path ({paths['Night Lord']['players']} Night Lord, {paths['Shadower']['players']} Shadower)",
      paths["Night Lord"]["players"] == 6 and paths["Shadower"]["players"] == 18)   # every Bandit (solo or not), Chief Bandit and Rogue
check(f"each path lists its jobs, 1st to 4th ({paths['Shadower']['steps']})",
      paths["Shadower"]["steps"] == ["Rogue", "Bandit", "Chief Bandit", "Shadower"])


# ---- 0.9.9 site: adaptive level range, % per hour, clean map names, towns, the ladder
def players_on(map_name, levels, exp_hr=40000, job="Bandit", party=1, day_n=2):
    for i, lv in enumerate(levels):
        call("/api/runs", {"install": install(), "runs": [run(t, exp_hr * rnd.uniform(0.95, 1.05), level=lv, map_name=map_name,
                                                              job=job, party=party) for t in hours(day(day_n), 4)]})


players_on("Rocky Road II", [50, 50, 51], 52000)                       # a busy level: 3 players within 3 levels
players_on("Sleepy Dungeon IV", [47, 52, 56], 61000)                   # a quiet one: needs the widest range
a = call("/api/stats?class=Thief&path=Shadower&level=50&party=solo")
byname = {m["map"]: m for m in a["maps"]}
check(f"a busy level stays narrow ({byname.get('Rocky Road II', {}).get('levels')})",
      byname.get("Rocky Road II", {}).get("levels") == [47, 53] and not byname["Rocky Road II"]["widened"])
check(f"a quiet level widens until the map has enough players ({byname.get('Sleepy Dungeon IV', {}).get('levels')})",
      byname.get("Sleepy Dungeon IV", {}).get("levels") == [42, 58] and byname["Sleepy Dungeon IV"]["widened"]
      and byname["Sleepy Dungeon IV"]["players"] == 3)
m = byname["Rocky Road II"]
check(f"each map has % of a level per hour and hours per level ({m['pct_hr']}%/hr, {m['hours_per_level']} h)",
      m["pct_hr"] > 0 and m["hours_per_level"] > 0 and abs(m["hours_per_level"] * m["pct_hr"] - 100) < 8
      and m["pct_hr_low"] <= m["pct_hr"] <= m["pct_hr_high"])
check("...and EXP/hr by level, for the detail panel", len(m["by_level"]) >= 2 and all(
      set(b) == {"level", "players", "exp_hr"} for b in m["by_level"]))
players_on("Rocky Road II", [50, 51, 49], 36000, party=3, day_n=3)    # the same map in a party
b = call("/api/stats?class=Thief&path=Shadower&level=50&party=solo&x=1")
m = {x["map"]: x for x in b["maps"]}["Rocky Road II"]
check(f"solo numbers show what a party gets there ({m['other']})",
      m["other"] and m["other"]["solo"] is False and m["other"]["players"] == 3 and 30000 < m["other"]["exp_hr"] < 42000)
check("...and the recent trend is there or empty, never made up",
      m["trend"] is None or set(m["trend"]) == {"recent", "before"})
check("the answer says when the newest run was shared", bool(a["newest_run"]) and a["newest_run"].endswith("Z"))
check("no install ids anywhere in the answer", "install" not in json.dumps(a))

# map names
players_on("ist Accompaniment Bonus", [35, 35, 36], 45000, day_n=4)
players_on("1st Accompaniment Bonus", [35, 36, 35], 46000, day_n=5)
c = call("/api/stats?class=Thief&path=Shadower&level=35&party=solo")
acc = [x for x in c["maps"] + c["early"] if "Accompaniment" in x["map"]]
check(f"'ist' and '1st' are one map ({[(x['map'], x['players']) for x in acc]})",
      [(x["map"], x["players"]) for x in acc] == [("1st Accompaniment Bonus", 6)])
r = call("/api/runs", {"install": install(), "runs": [run(hours(day(6), 1)[0], 45000, map_name="Perion")]})
check(f"a town is turned down ({r['results'][0].get('why')})", r["received"] == 0 and r["results"][0].get("why") == "town")
r = call("/api/runs", {"install": install(), "runs": [run(hours(day(6), 1)[0], 45000, map_name="Henesys")]})
check("...another town too", r["received"] == 0 and r["results"][0].get("why") == "town")
r = call("/api/runs", {"install": install(), "runs": [run(hours(day(6), 1)[0], 45000, map_name="Henesys Hunting Ground II")]})
check("...but a hunting map with a town's name in it is fine", r["received"] == 1)
event = [install() for _ in range(3)]
for who in event:
    call("/api/runs", {"install": who, "runs": [run(t, 90000, level=40, map_name="Halloween Event Field") for t in hours(day(7), 4)]})
d = call("/api/stats?class=Thief&path=Shadower&level=40&party=solo")
check("a map that isn't on the game's list (an event map) is accepted but hidden after one day of players",
      not any(x["map"] == "Halloween Event Field" for x in d["maps"] + d["early"]))
for who in event:                                        # (and again "the next day": the server's clock moved on)
    call("/api/runs", {"install": who, "runs": [run(t, 90000, level=40, map_name="Halloween Event Field") for t in hours(day(8), 4)]}, later=26)
d = call("/api/stats?class=Thief&path=Shadower&level=40&party=solo")
check("...and shown once 3 players have shared it on 2 days", any(x["map"] == "Halloween Event Field" for x in d["maps"]))
two = [install() for _ in range(2)]
for who in two:
    for dn, later in ((9, None), (10, 26)):
        call("/api/runs", {"install": who, "runs": [run(t, 70000, level=40, map_name="Totally Another Place") for t in hours(day(dn), 4)]}, later=later)
d = call("/api/stats?class=Thief&path=Shadower&level=40&party=solo")
check("...but 2 players over 2 days aren't enough for a name the game doesn't list",
      not any(x["map"] == "Totally Another Place" for x in d["maps"] + d["early"]))

# the ladder
ladder = call("/api/ladder?class=Thief&path=Shadower&party=solo")
bands = {b["from"]: b for b in ladder.get("bands", [])}
check(f"the ladder has a band of 5 levels with the best maps ({list(bands)[:6]})",
      ladder.get("band") == 5 and 50 in bands and bands[50]["to"] == 54 and bands[50]["maps"][0]["map"] in ("Rocky Road II", "Sleepy Dungeon IV"))
check("...each with EXP/hr, %/hr and players, best first", all(
      set(mm) == {"map", "exp_hr", "pct_hr", "mesos_hr", "players"} and mm["players"] >= 3
      for b in ladder["bands"] for mm in b["maps"]) and all(
      [mm["exp_hr"] for mm in b["maps"]] == sorted((mm["exp_hr"] for mm in b["maps"]), reverse=True) for b in ladder["bands"]))
check("the ladder needs a class", call("/api/ladder?party=solo").get("status") == 400)
check("...and a real one", call("/api/ladder?class=constructor").get("status") == 400
      and call("/api/ladder?class=Thief&path=__proto__").get("status") == 400 and call("/api/ladder?class=Thief&party=9").get("status") == 400)


# ---- hardening: made-up players, made-up names, floods
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def sql(command):
    """Rows from a query on the LOCAL test database (the same one the test server uses)."""
    wrangler = os.path.join(HERE, "node_modules", ".bin", "wrangler.cmd" if os.name == "nt" else "wrangler")
    out = subprocess.run([wrangler, "d1", "execute", "classicexp", "--local", "--persist-to", ".wrangler/test-state", "--json",
                          "--command", command], cwd=HERE, capture_output=True, text=True, timeout=120).stdout
    start = out.index("[")
    return json.loads(out[start:])[0]["results"]


def stats(level, later=None, cls="Thief", path="Shadower", party="solo"):
    return call(f"/api/stats?class={cls}&path={path.replace(' ', '%20')}&level={level}&party={party}", later=later)


def seen(level, name, **kw):
    d = stats(level, **kw)
    return any(x["map"] == name for x in d["maps"] + d["early"])


# a new player doesn't count for a day, nor with fewer than 4 runs
crowd = [install() for _ in range(5)]
for who in crowd:
    r = call("/api/runs", {"install": who, "runs": [run(t, 61000, level=55, map_name="Cold Field II") for t in hours(day(12), 4)]})
    assert r.get("received") == 4
check("five brand-new players are stored but not shown an hour later", not seen(55, "Cold Field II", later=1))
check("...they show once a day has passed", seen(55, "Cold Field II", later=25))
few = install()
call("/api/runs", {"install": few, "runs": [run(t, 61000, level=71, map_name="Cold Field II") for t in hours(day(12), 3)]})
check("a player with only 3 runs never counts, however long it's been", not seen(71, "Cold Field II", later=500))
before = call("/api/options?fresh=1", later=1)["players"]
after = call("/api/options?fresh=1", later=25)["players"]
check(f"the player count on the page leaves out new players ({before} an hour later, {after} a day later)", after >= before + 5)

# made-up players from one IPv6 home: the whole /64 is one connection
six = [install() for _ in range(8)]
got = [call("/api/runs", {"install": who, "runs": [run(f"{day(13)}T08:00", 50000, map_name="Ant Tunnel II")]},
            ip=f"2001:db8:1:2:{i:x}:{i * 7:x}::{i:x}")["results"][0] for i, who in enumerate(six)]
check(f"billions of IPv6 addresses at one home are one connection: only 6 players get through ({[g['ok'] for g in got]})",
      sum(g["ok"] for g in got) == 6 and got[-1].get("retry") is True)
ok_other = call("/api/runs", {"install": install(), "runs": [run(f"{day(13)}T09:00", 50000, map_name="Ant Tunnel II")]},
                ip="2001:db8:1:3::1")["results"][0]
check("...while another home next door is its own connection", ok_other["ok"])
mapped = [call("/api/runs", {"install": install(), "runs": [run(f"{day(14)}T08:00", 50000, map_name="Ant Tunnel II")]},
               ip=f"::ffff:198.51.100.{9}")["results"][0] for _ in range(7)]
check("an IPv4 address written the IPv6 way is still the same IPv4 connection", sum(g["ok"] for g in mapped) == 6)

# what a run may claim
who = install()


def bad(**kw):
    r = call("/api/runs", {"install": who, "runs": [dict(run(f"{day(15)}T10:00", 50000), **kw)]})
    return r["results"][0].get("why")


check(f"a job the game doesn't have is refused ({bad(**{'class': 'Free Mesos Seller'})})", bad(**{"class": "Free Mesos Seller"}) == "not a job")
check("...and a real one is fine", call("/api/runs", {"install": who, "runs": [run(f"{day(15)}T11:00", 50000)]})["received"] == 1)
look_alike = bad(map_read="Ant Tunnel IⅠАnt Tunnel")
check(f"a map name with letters outside English is refused ({look_alike})", look_alike == "not a map name")
check("...so is an advert", bad(map_read="Join our discord for free mesos now") == "not a map name"
      and bad(map_read="Visit discord.gg slash maple fans") == "not a map name" and bad(map_read="Best prices telegram channel") == "not a map name")
check("...and a real map with the word 'free' in it is fine", call("/api/runs", {"install": who, "runs": [run(f"{day(15)}T12:00", 50000, map_name="Free Market 3")]})["results"][0].get("why") == "town")
check("a run gaining 100 levels' worth of mesos an hour is refused",
      bad(mesos=9e9) == "impossible mesos/hr")
check("a Lv 200 run claiming 5% of the table's end a minute is refused", bad(level_start=200, level_end=200, exp=2e9) in
      ("impossible EXP for that level", "impossible EXP for those levels"))
dozen = install()
names = [l.split("\t")[0] for l in open(os.path.join(HERE, "..", "assets", "maps.txt"), encoding="utf-8") if l.rstrip().endswith("\t0")][:14]
results = [call("/api/runs", {"install": dozen, "runs": [run(f"{day(16)}T{8 + i // 4:02d}:{(i % 4) * 15:02d}", 50000, map_name=n)]})
           ["results"][0] for i, n in enumerate(names)]
check(f"one install can't farm more than 12 different maps in a day ({sum(r['ok'] for r in results)} of 14)",
      sum(r["ok"] for r in results) == 12 and results[-1].get("why") == "too many different maps in a day")

# the server's clock can't be set from outside: the live site ignores the test header
check("the test header is only for a local server (the check is in the code)",
      "x-test-now" in open(os.path.join(HERE, "src", "index.js"), encoding="utf-8").read().lower()
      and "localhost" in open(os.path.join(HERE, "src", "index.js"), encoding="utf-8").read())

# a banned install: refused, and gone from the numbers
villain = install()
call("/api/runs", {"install": villain, "runs": [run(t, 61000, level=90, map_name="Cold Field I") for t in hours(day(17), 4)]})
check("a player with runs shows a day later", seen(90, "Cold Field I", later=25))
sql(f"UPDATE installs SET banned = 1 WHERE install = '{villain}'")
r = call("/api/runs", {"install": villain, "runs": [run(f"{day(17)}T20:00", 61000, level=90, map_name="Cold Field I")]})
check(f"a banned install is refused ({r['results'][0].get('why')})", r["received"] == 0 and r["results"][0].get("why") == "refused"
      and not r["results"][0].get("retry"))
check("...and its runs are left out of the numbers", not seen(90, "Cold Field I", later=25))

# a flood costs nothing: broken posts make no database rows
rows_before = sql("SELECT (SELECT COUNT(*) FROM installs) AS i, (SELECT COALESCE(SUM(new_installs), 0) FROM daily) AS d, (SELECT COUNT(*) FROM map_review) AS m")[0]
ghost = install()
for body in ({"install": ghost, "runs": [{"x": 1}]}, {"install": ghost, "runs": [dict(run(f"{day(18)}T10:00", 50000), **{"class": "x"})]},
             {"install": ghost, "runs": [dict(run(f"{day(18)}T10:00", 50000), map_read="Join discord now")]}, {"install": ghost, "runs": []}):
    call("/api/runs", body)
rows_after = sql("SELECT (SELECT COUNT(*) FROM installs) AS i, (SELECT COALESCE(SUM(new_installs), 0) FROM daily) AS d, (SELECT COUNT(*) FROM map_review) AS m")[0]
check(f"posts with nothing valid in them leave no trace in the database ({rows_before} -> {rows_after})", rows_before == rows_after)
check("...and the player never became known", sql(f"SELECT COUNT(*) AS n FROM installs WHERE install = '{ghost}'")[0]["n"] == 0)

# a flood of brand-new players is stopped for the day, while players the server knows carry on
known = install()
call("/api/runs", {"install": known, "runs": [run(f"{day(19)}T08:00", 50000, map_name="Ant Tunnel II")]})
stopped = None
for i in range(420):
    r = call("/api/runs", {"install": install(), "runs": [run(f"{day(19)}T09:00", 50000, map_name="Ant Tunnel II")]}, ip=f"203.0.{i // 200}.{i % 200 + 1}")
    if r["results"][0].get("retry") and "new players" in r["results"][0].get("why", ""):
        stopped = i
        break
check(f"after a few hundred new players in one day the rest are asked to come back tomorrow (stopped after {stopped})",
      stopped is not None and 50 < stopped < 420)
r = call("/api/runs", {"install": known, "runs": [run(f"{day(19)}T09:15", 50000, map_name="Ant Tunnel II")]})
check("...but a player the server already knows still gets through", r["received"] == 1)

# hostile names never reach the page as anything but text (and don't break the answers)
hostile = install()
call("/api/runs", {"install": hostile, "runs": [run(t, 50000, level=60, map_name="<img src=x onerror=alert(1)> Cave") for t in hours(day(20), 4)]})
body = json.dumps(call("/api/stats?class=Thief&path=Shadower&level=60&party=solo", later=25))
check("a name with HTML in it has its angle brackets removed", "<img" not in body and "<script" not in body)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

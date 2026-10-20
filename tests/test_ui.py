"""Drives the real overlay window (it needs a screen, so it isn't part of test_logic.py): the notes
box under the log. Run from the project folder:  py -3 tests/test_ui.py

It uses its own temporary data folder and never goes online."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

DATA = Path(tempfile.mkdtemp())
os.environ["MAPLE_EXP_DATA"] = str(DATA)                    # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maple_exp as m  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def farm(log, t0, seconds, party=1, map_name="Rocky Road II", level=27):
    t, nxt = t0, t0
    while t < t0 + seconds:
        if t >= nxt:
            log.gain(t, 100)
            nxt = t + 3
        log.tick(t, map_name, level, party, None, "Bandit")
        t += 0.5
    return t


def on_disk():
    return {(e["map"], e["party"]): e.get("note") for e in
            json.loads((DATA / "exp_log.json").read_text(encoding="utf-8"))["entries"]}


# a log to look at: one solo line, and one map played in parties of 4 and 5 (shown as one line)
seed = m.AutoLog(15, m.LOG_PATH, m.LOG_TEXT_PATH)
t = farm(seed, 0, 310, party=1, map_name="Ant Tunnel I")
t = farm(seed, t + 60, 310, party=4, map_name="Wild Boar Land II", level=30)
t = farm(seed, t + 60, 310, party=5, map_name="Wild Boar Land II", level=30)
m.save_config({**m.DEFAULTS, "log_open": True, "pos": [60, 60], "hotkeys_on": False, "check_updates": False,
               "share_runs": False})

root = m.tk.Tk()
app = m.App(root, m.DigitTemplates.load())
root.update()
tree, box = app.log_tree, app.note_text


def pump(seconds=0.0):
    end = time.time() + seconds
    root.update()
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def row(map_name, group=False):
    for iid in tree.get_children():
        if tree.item(iid, "values")[1] == map_name and (iid in app.groups) == group:
            return iid


def select(iid):
    tree.selection_set(iid)
    pump(0.1)


def type_note(text):
    box.insert("end", text)
    pump(0.1)


solo, group = row("Ant Tunnel I"), row("Wild Boar Land II", group=True)
check("the log has a solo line and one shared party line", solo and group)
check("the Note column is there, empty to begin with",
      "note" in tree["columns"] and tree.item(solo, "values")[8] == "" and tree.item(group, "values")[8] == "")
check("with no line selected, the note box can't be typed in", str(box.cget("state")) == "disabled")

# a solo line
select(solo)
check("selecting a line opens its note", str(box.cget("state")) == "normal" and "Ant Tunnel I" in app.note_title.cget("text")
      and "only" in app.note_title.cget("text").lower())
type_note("Good drops, bring pots!")
check("what's typed isn't saved at once (it waits for a pause)", on_disk()[("Ant Tunnel I", 1)] is None)
pump(1.3)
check("...but a moment later it's saved in the log file", on_disk()[("Ant Tunnel I", 1)] == "Good drops, bring pots!")
check("...and the line gets its mark", tree.item(solo, "values")[8] == m.NOTE_MARK and "saved" in app.note_title.cget("text"))

# moving to another line saves the one left, and shows the other's note
type_note(" More")
select(group)
check("clicking another line saves the note being typed", on_disk()[("Ant Tunnel I", 1)] == "Good drops, bring pots! More")
check("the other line's box is empty and ready", box.get("1.0", "end-1c") == "" and str(box.cget("state")) == "normal")

# a shared party line: one note for all of its party sizes
type_note("Pt of 4-5 works.\nKS-ing.")
select(solo)
check("a note on a shared party line goes on each party size", on_disk()[("Wild Boar Land II", 4)] == on_disk()[("Wild Boar Land II", 5)]
      == "Pt of 4-5 works.\nKS-ing.")
check("...and shows on the shared line", tree.item(group, "values")[8] == m.NOTE_MARK)
tree.item(group, open=True)
app._sync_group(group)
child = tree.get_children(group)[0]
select(child)
check("opening it, each party size shows the same note", box.get("1.0", "end-1c") == "Pt of 4-5 works.\nKS-ing."
      and tree.item(child, "values")[8] == m.NOTE_MARK)

# the log refreshes by itself every few minutes: that must not disturb a note being typed
select(solo)
box.mark_set("insert", "1.4")
type_note("\n(typed while the log refilled)")
before = box.get("1.0", "end-1c")
app._fill_log()
pump(0.3)
check("a refilled log keeps the same line selected", tree.selection() == (row("Ant Tunnel I"),))
check("...keeps what's being typed", box.get("1.0", "end-1c") == before)
check("...and saved it first", on_disk()[("Ant Tunnel I", 1)] == before)

# renaming a map keeps its note
app.autolog.rename(app.autolog.entries[[e["map"] for e in app.autolog.entries].index("Ant Tunnel I")], "Ant Tunnel One")
app._fill_log()
pump(0.2)
check("renaming a map keeps its note", on_disk()[("Ant Tunnel One", 1)] == before and tree.item(row("Ant Tunnel One"), "values")[8] == m.NOTE_MARK)
check("...and the box goes empty (that line is gone under its old name)", str(box.cget("state")) == "disabled")

# typing needs the hotkeys out of the way: Shift+1 is "!"
select(row("Ant Tunnel One"))
box.event_generate("<FocusIn>")
pump(0.1)
check("hotkeys are off while typing a note", getattr(app.hotkeys, "_paused", True) is True)
box.event_generate("<FocusOut>")
pump(0.1)
check("...and back on after", getattr(app.hotkeys, "_paused", False) is False)

# an over-long note is cut to the limit, in the box too
select(row("Ant Tunnel One"))
box.delete("1.0", "end")
box.insert("1.0", "x" * (m.NOTE_MAX + 500))
pump(1.3)
check("a note longer than the limit is cut to it", len(box.get("1.0", "end-1c")) == m.NOTE_MAX
      and len(on_disk()[("Ant Tunnel One", 1)]) == m.NOTE_MAX)

# deleting a line with a note says so
asked = []
m.messagebox.askyesno = lambda title, text, **k: asked.append(text) or False
app._delete([app.shown[row("Ant Tunnel One")]])
check("deleting a line that has a note says its note goes too", asked and "note on it will be deleted too" in asked[0])

# closing the overlay (or hiding the log) saves a note still being typed
select(row("Ant Tunnel One"))
box.delete("1.0", "end")
type_note("last words")
app.set_compact(True)
check("switching to compact mode saves a note being typed", on_disk()[("Ant Tunnel One", 1)] == "last words")
app.set_compact(False)
pump(0.3)
select(row("Ant Tunnel One"))
type_note(" and one more")
app.close()
check("closing the overlay saves a note being typed", on_disk()[("Ant Tunnel One", 1)] == "last words and one more")
saved = json.loads((DATA / "exp_log.json").read_text(encoding="utf-8"))["entries"]
check("every line is still as the overlay wrote it (notes don't break the seal)",
      all(not m.AutoLog(15, m.LOG_PATH, m.LOG_TEXT_PATH).edited(e) for e in saved))

# ---- the design: every size, every state, and awkward values never break the window
root = m.tk.Tk()
m.save_config({**m.DEFAULTS, "hotkeys_on": False, "check_updates": False, "share_runs": False, "pos": [60, 60]})
app = m.App(root, m.DigitTemplates.load())
t, meso = app.tracker, app.meso
sizes_ok, widths = True, {}
for scale in m.UI_SCALES:
    try:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for case in ("idle", "tracking", "huge", "compact", "details"):
            if case == "idle":
                t.exp = t.pct = t.req = None
                t.rate = lambda: None
                t.rolling_rate = lambda: None
            else:
                t.exp, t.pct, t.req, t.gained, t.levels = 31204, 54.2, 57600, 412300, 2
                huge = case == "huge"
                t.rate = lambda huge=huge: 123456789.0 if huge else 72566.0
                t.rolling_rate = lambda huge=huge: 99999999.0 if huge else 71900.0
                t.elapsed = lambda: 5620.0
                meso.value, meso.gained = 18449220, 41000
                app.level = 35
            app.set_compact(case == "compact")
            if case == "details" and not app.view.details_open:
                app.view._toggle_details()
            app.session.on = case != "idle"
            app._update_display()
            root.update()
            widths[scale] = app.view.winfo_width()
    except Exception as e:                                   # (printed, so a failure says where)
        print("   error at", scale, "%:", type(e).__name__, e)
        sizes_ok = False
check("the window draws at every size (80% to 200%) and in every state: idle, tracking, huge numbers, compact, details", sizes_ok)
check(f"a bigger size makes the window wider, not clipped ({widths})",
      all(widths[a] < widths[b] for a, b in zip(m.UI_SCALES, m.UI_SCALES[1:])))
app.view.show(rate="123,456,789", state="alert", state_text="Can't read the EXP bar",
              message="A very long message " * 12, tiles={"Last 5 min": "99,999,999", "Mesos / hr": "-", "Elapsed": "123:59:59"})
root.update()
check("long messages and big numbers still fit the window", app.view.winfo_reqwidth() <= app.theme.px(400) + 4)
app.set_compact(False)
app.set_compact(True)
app._update_display()
hero = app.view.hero
check("compact mode shows mesos/hr under the number, and only there",
      hero.itemcget(hero._mval, "state") == "normal" and hero.itemcget(hero._mval, "text") != "")
app.set_compact(False)
app._update_display()
check("full mode doesn't show that line (it has the tile)", hero.itemcget(hero._mval, "state") == "hidden")

# ---- the compact shapes: Square, Wide and Bar
import ui_main  # noqa: E402
t.exp, t.pct, t.req, t.gained, t.levels = 31204, 54.2, 57600, 412300, 2
t.rate, t.rolling_rate, t.elapsed = (lambda: 72566.0), (lambda: 71900.0), (lambda: 5620.0)
meso.value, meso.gained = 18449220, 41000
app.level, app.session.on = 35, True
meso.per_hour = lambda el: 28830.0
app._state = lambda: ("tracking", "Tracking")
shapes_ok, fits = True, True
for scale in (60, 100, 200):
    app.cfg["ui_scale"] = scale
    app._apply_ui_scale()
    for shape in m.SHAPES:
        try:
            app.set_shape(shape)
            app.set_compact(True)
            app._update_display()
            root.update()
            want = app.theme.px(ui_main.SHAPE_W[shape])
            fits = fits and abs(app.view.winfo_reqwidth() - want) <= 4 and root.winfo_width() >= want - 4
        except Exception as e:
            print("   error at", scale, "%", shape, ":", type(e).__name__, e)
            shapes_ok = False
    app.set_compact(False)
root.update()
check("every compact shape draws at 60%, 100% and 200%", shapes_ok)
check("...and each window is as wide as its shape says (Square 360, Wide 560, Bar 720)", fits)
app.cfg["ui_scale"] = 100
app._apply_ui_scale()
app.set_shape("wide")
app.set_compact(True)
app._update_display()
root.update()
wide = app.view.strips["wide"]
shown = [wide.canvas.itemcget(i, "text") for i in wide._vals]
check("Wide shows last 5 min, mesos/hr and time to the next level", shown[0] == "71,900" and shown[1] and shown[2] not in ("", "\u2014"))
check("...with the level to reach in its heading", wide.canvas.itemcget(wide._caps[3], "text") == "TO LV 36")
check("Wide has no big hero or tiles on screen", not app.view.hero.winfo_ismapped() and not app.view.tiles_row.winfo_ismapped())
app.set_shape("bar")
app._update_display()
root.update()
bar = app.view.strips["bar"]
check("Wide gives way to Bar when the shape changes in compact mode", bar.winfo_ismapped() and not wide.winfo_ismapped())
check("Bar shows mesos/hr and the level to reach", bar.canvas.itemcget(bar._mval, "text") not in ("", "\u2014")
      and "Lv 36" in bar.canvas.itemcget(bar._eta, "text"))
check("Bar has no status line while tracking...", not bar.message.winfo_ismapped())
app._state = lambda: ("alert", "Can't read the EXP bar")
app.status.set("Can't find the EXP bar.")
app._update_display()
root.update()
check("...but shows it when something needs attention", bar.message.winfo_ismapped())
app._state = lambda: ("tracking", "Tracking")
app.status.set("Tracking.")
app._update_display()
app._set_start("Pause")
check("Start/Pause on the sideways shapes follows the real button", bar.start_btn.icon == "pause" and wide.start_btn.icon == "pause"
      and wide.start_btn.text == "Pause")
app._set_start("Resume")
check("...and the shape made later starts in the right state", app.view.strips["bar"].start_btn.icon == "play")
bar.compact_btn.command()
root.update()
check("the compact icon on a sideways shape goes back to full", not app.compact and app.view.head.winfo_ismapped()
      and not bar.winfo_ismapped() and app.view.hero.winfo_ismapped())
check("full mode is as wide as ever whichever shape is chosen", abs(app.view.winfo_reqwidth() - app.theme.px(ui_main.W)) <= 4)
check("the shape is saved", json.loads((DATA / "config.json").read_text(encoding="utf-8"))["compact_shape"] == "bar")
app.set_shape("square")
app._set_start("Start")

# first launch: a new install picks a shape, an update shows what's new, and each only once
calls = []
app._shape_picker = lambda then: calls.append("picker") or then()
app._whats_new = lambda notes, then: calls.append("notes") or then()
app._ask_to_share = lambda: calls.append("share")


def first_run(fresh, seen):
    calls.clear()
    app._fresh, app.cfg["seen_version"] = fresh, seen
    app._first_run()
    return list(calls), app.cfg["seen_version"]


check("a new install picks a compact shape, then gets the sharing question", first_run(True, "") == (["picker", "share"], m.VERSION))
check("someone updating sees what's new once, then the sharing question", first_run(False, "0.9.9-beta") == (["notes", "share"], m.VERSION))
check("an existing config from before this feature counts as an update", first_run(False, "") == (["notes", "share"], m.VERSION))
check("the same version shows neither again", first_run(False, m.VERSION) == (["share"], m.VERSION))
check("going back to an older version shows nothing and keeps what was seen", first_run(False, "0.9.99") == (["share"], "0.9.99"))
check("what's new has a note for this version", bool(m.WHATS_NEW.get(m.VERSION)))
picked = []
selector = m.RegionSelector(root, picked.append)             # (it crashed on Windows in 0.9.7 and 0.9.8-beta)
root.update()
check("Select area opens its full-screen picker", selector.win.winfo_exists())
selector._finish(None)
check("...and Esc closes it without choosing anything", picked == [None] or picked == [])
import numpy as np
fake = m.Reading(*([None] * len(m.Reading._fields)))._replace(
    where="MapleStory window 1920x1080", text="3,060 [1.04%]", image=np.full((40, 200, 3), 60, np.uint8), boxes=[])
app.last_capture = fake
app.job = None
app.reader.job_look = {"image": np.full((60, 300, 3), 90, np.uint8), "boxes": [(10, 10, 80, 40, "exp")],
                       "why": "the tries didn't agree on what it says"}
before = set(root.winfo_children())
app.view_capture()
root.update()
win = next(w for w in set(root.winfo_children()) - before)
texts = " | ".join(w.cget("text") for w in win.winfo_children() if w.winfo_class() == "Label" and w.cget("text"))
check("View capture says why the class wasn't read", "Class. Not read: the tries didn't agree" in texts)
win.destroy()
app.reader.job_look = {"image": None, "boxes": [], "why": "this window is covering the class name: move it"}
app.view_capture(); root.update()
win = next(w for w in set(root.winfo_children()) - before)
check("...and works when there is no picture (the overlay is in the way)",
      "covering the class name" in " ".join(w.cget("text") for w in win.winfo_children() if w.winfo_class() == "Label"))
win.destroy()
app.reader.box = (700, 1000, 200, 30)                          # (the EXP bar is found: only then is the class worth asking about)
files = app._save_report_files(False)
text = app._report_text(files)
check("a bug report says why the class wasn't read", "Class not read: this window is covering the class name" in text)
check("it saves a picture, and the details as a text file", files["picture"].exists() and files["details"].exists())
check("...with the whole story in the text file", all(w in files["details"].read_text(encoding="utf-8")
                                                      for w in ("== Game window ==", "== What it found ==", "== What happened")))
check("the web address a report is opened with stays short enough",
      len(m.urllib.parse.urlencode({"title": "x", "body": text})) < 7000)
check("it never contains the Windows user name",
      not any(len(s) > 2 and s in text + files["details"].read_text(encoding="utf-8")
              for s in {str(m.Path.home()), m.os.environ.get("USERNAME", "")}))
print(text)
app.job, app.reader.job_look, app.last_capture = None, None, None
app.reader.box = None
# ---- every Size at every Windows scaling: nothing drawn outside its box, nothing cut off, nothing broken
def spill(widget, slack=2):
    """Texts or pictures that stick out of the canvas they're drawn in (listed by kind)."""
    bad = []
    for canvas in [widget] + [c for c in widget.winfo_children()]:
        stack = [canvas]
        while stack:
            c = stack.pop()
            stack.extend(c.winfo_children())
            if c.winfo_class() != "Canvas" or not c.winfo_ismapped():
                continue
            c.update_idletasks()
            w, h = c.winfo_width(), c.winfo_height()
            for item in c.find_all():
                if c.type(item) != "text" or c.itemcget(item, "state") == "hidden" or not c.itemcget(item, "text"):
                    continue
                x0, y0, x1, y1 = c.bbox(item)
                if x0 < -slack or y0 < -slack or x1 > w + slack or y1 > h + slack:
                    bad.append(f"{c.itemcget(item, 'text')[:18]!r} {x0},{y0},{x1},{y1} in {w}x{h}")
    return bad


app._check_screen = lambda: None                          # (it would put the screen's own scaling back)
layout_bad = []
for dpi in (96, 120, 144, 192):                          # Windows scaling 100%, 125%, 150%, 200%
    app._dpi = dpi
    for scale in m.UI_SCALES:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for case in ("tracking", "huge", "compact", "details"):
            t.exp, t.pct, t.req, t.gained, t.levels = 31204, 54.2, 57600, 412300, 2
            huge = case == "huge"
            t.rate = lambda huge=huge: 123456789.0 if huge else 72566.0
            t.rolling_rate = lambda huge=huge: 99999999.0 if huge else 71900.0
            t.elapsed = lambda: 5620.0
            meso.value, meso.gained = 18449220, 41000
            app.level = 35
            app.set_compact(case == "compact")
            if case == "details" and not app.view.details_open:
                app.view._toggle_details()
            if case != "details" and app.view.details_open:
                app.view._toggle_details()
            app.session.on = True
            app._update_display()
            root.update()
            for line in spill(app.view):
                layout_bad.append(f"{dpi}dpi {scale}% {case}: {line}")
        app.set_compact(False)
check("at every Size and Windows scaling, no text sticks out of its box on the main window"
      + (f" ({len(layout_bad)} problems, first: {layout_bad[:3]})" if layout_bad else ""), not layout_bad)

fonts_small = []
for dpi in (96, 120, 144, 192):
    app._dpi = dpi
    for scale in m.UI_SCALES:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for name, f in app.fonts.items():
            if abs(int(f.cget("size"))) < 9:
                fonts_small.append(f"{dpi}dpi {scale}% {name} {f.cget('size')}")
        for (weight, size), f in app.theme._tk.items():
            if abs(int(f.cget("size"))) < m.MIN_TEXT_PX if hasattr(m, "MIN_TEXT_PX") else False:
                fonts_small.append(f"{dpi}dpi {scale}% {weight} {size}")
check(f"text never gets smaller than the readable minimum ({fonts_small[:3]})", not fonts_small)

# the window, the log and Settings still fit their contents at the smallest and biggest sizes
fit_bad = []
for dpi in (96, 192):
    app._dpi = dpi
    for scale in (60, 70, 100, 200):
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        app.set_compact(False)
        if not app.log_open:
            app.toggle_log()
        root.update()
        tree = app.log_tree
        headfont = m.tkfont.nametofont("TkHeadingFont") if "TkHeadingFont" in m.tkfont.names(root) else app.fonts["text"]
        for col in tree["columns"]:
            need = app.fonts["text"].measure(tree.heading(col, "text") or "") + 6
            if int(tree.column(col, "width")) + 2 < min(need, 40):
                fit_bad.append(f"{dpi}dpi {scale}% log column {col}")
        app.open_settings()
        sw = app.settings_win
        sw.geometry("")
        sw.update()
        for child in sw.winfo_children():
            stack = [child]
            while stack:
                c = stack.pop()
                stack.extend(c.winfo_children())
                if c.winfo_ismapped() and (c.winfo_rootx() + c.winfo_width() > sw.winfo_rootx() + sw.winfo_width() + 2
                                           or c.winfo_rooty() + c.winfo_height() > sw.winfo_rooty() + sw.winfo_height() + 2):
                    fit_bad.append(f"{dpi}dpi {scale}% settings: {c.winfo_class()} sticks out")
        for line in spill(sw):
            fit_bad.append(f"{dpi}dpi {scale}% settings: {line}")
        sw.destroy()
        app.settings_win = None
        app.toggle_log()
check(f"the log and Settings fit at the smallest and biggest Sizes ({fit_bad[:3]})", not fit_bad)
app._dpi = 192 if app._dpi not in (96, 120, 144, 192) else app._dpi
app.cfg["ui_scale"] = 100
app._apply_ui_scale()

# ---- Size and Transparency limits
check("sizes from 60% to 200% are offered", m.UI_SCALES[0] == 60 and m.UI_SCALES[1] == 70 and m.UI_SCALES[-1] == 200)
saved = m.CONFIG_PATH
cfg_file = Path(tempfile.mkdtemp()) / "config.json"
cfg_file.write_text(json.dumps({"transparency": 90, "ui_scale": 70}), encoding="utf-8")
m.CONFIG_PATH = cfg_file
loaded = m.load_config()
m.CONFIG_PATH = saved
check("an older config with transparency 90 loads as the 40% limit, and a saved 70% size is kept",
      loaded["transparency"] == 40 and loaded["ui_scale"] == 70)
cfg_file.write_text(json.dumps({"transparency": -5, "ui_scale": 65}), encoding="utf-8")
m.CONFIG_PATH = cfg_file
loaded = m.load_config()
m.CONFIG_PATH = saved
check("a negative transparency is 0, and a size that isn't offered is 100%", loaded["transparency"] == 0 and loaded["ui_scale"] == 100)
check("transparency can't go past 40%", m.MAX_TRANSPARENCY == 40)

# ---- the transparency slider stops at the limit, and the window can't fade past it
app.cfg["transparency"] = 90
app._apply_window_settings()
check("the window is never more see-through than the limit (opacity %.2f)" % float(root.attributes("-alpha")),
      float(root.attributes("-alpha")) >= 1 - m.MAX_TRANSPARENCY / 100 - 0.01)
app.cfg["transparency"] = 0
app._apply_window_settings()

# ---- hotkeys in Settings: one waiting at a time, and clicking it again cancels
if app.hotkeys.supported:
    app.open_settings()
    sw = app.settings_win
    sw.update()
    ui = app._hotkey_ui
    names = list(ui["buttons"])
    keys_before = dict(app.cfg["hotkeys"])
    texts = lambda: {a: b.itemcget(b._txt, "text") for a, b in ui["buttons"].items()}
    ui["choose"](names[0])
    check("clicking a hotkey makes it wait for keys", ui["choosing"]["action"] == names[0] and texts()[names[0]] == "Press keys...")
    ui["choose"](names[1])
    t2 = texts()
    check("clicking another one moves the wait to it: only one says Press keys",
          ui["choosing"]["action"] == names[1] and sum(v == "Press keys..." for v in t2.values()) == 1
          and t2[names[0]] == (keys_before[names[0]] or "Off"))
    ui["choose"](names[1])
    check("clicking the waiting one again cancels it, and every key is as it was",
          ui["choosing"]["action"] is None and texts() == {a: (keys_before[a] or "Off") for a in names}
          and app.cfg["hotkeys"] == keys_before)
    check("hotkeys work again after cancelling", getattr(app.hotkeys, "_paused", True) is False)
    ui["choose"](names[2])
    esc = type("E", (), {"keysym": "Escape", "state": 0, "keycode": 27})()
    ui["pressed"](esc)
    check("Esc cancels too, keeping the key", ui["choosing"]["action"] is None and app.cfg["hotkeys"] == keys_before)
    ui["choose"](names[3])
    back = type("E", (), {"keysym": "BackSpace", "state": 0, "keycode": 8})()
    ui["pressed"](back)
    check("Backspace turns that hotkey off", app.cfg["hotkeys"][names[3]] == "" and ui["choosing"]["action"] is None)
    app.cfg["hotkeys"].update(keys_before)
    m.save_config(app.cfg)
    ui["choose"](names[0])
    ui["on"].set(False)
    sw.update()
    check("turning hotkeys off while one is waiting ends the wait", ui["choosing"]["action"] is None)
    ui["on"].set(True)
    sw.update()
    sw.destroy()
    app.settings_win = None
    check("closing Settings leaves hotkeys working", getattr(app.hotkeys, "_paused", True) is False)

# ---- towns aren't logged; a minimap that can't be seen pauses
blank = m.Reading(*([None] * len(m.Reading._fields)))
app._handle(blank._replace(where="Reading", map="Perion"))
check("on a town map the status says nothing is logged", app.in_town and "In a town" in app.status.get())
entries = len(app.autolog.entries)
app._log_tick(m.time.monotonic())
check("...and nothing is logged there", len(app.autolog.entries) == entries)
app._handle(blank._replace(where="Reading", map="Rocky Road II"))
check("on a hunting map it's normal again", not app.in_town and "In a town" not in app.status.get())
real_ok = m.tesseract_ok
m.tesseract_ok = True
app.session.on, app.session.waiting = True, "idle"
app.reader.map_seen_at = m.time.monotonic() - m.MAP_BLIND_SECONDS - 5
app._handle(blank._replace(where="Reading"))
check("a minimap name unseen for a while pauses it", app.session.waiting == "blind")
app.reader.map_seen_at = m.time.monotonic()
app.session.on, app.session.waiting = False, None
m.tesseract_ok = real_ok

check("the typeface is the bundled one, or it fell back cleanly", app._own_fonts in (True, False) and bool(app.theme.fam["extra"]))
app.close()

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

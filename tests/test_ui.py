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

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)

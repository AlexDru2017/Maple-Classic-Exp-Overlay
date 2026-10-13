# Changelog

## 0.9.4-beta

**New**
- **Best maps** link at the top of the overlay: opens the best-maps website already showing the maps for your level, party size and class.
- **Runs for the best-maps site are 15 minutes** instead of an hour, so more of your farming counts (a break or a new map no longer throws away up to an hour).
- **Smaller download:** 82 MB instead of 98 MB. Parts of the libraries it never used are left out.
- **The best-maps website** fills in sooner: it now looks 5 levels either way, and counts parties of any size together (solo or party).
- Downloads can now be built on GitHub straight from the code, the first step toward code signing (so Windows stops warning about new downloads).

## 0.9.3-beta

**New**
- **New name: Maple Classic Exp Overlay** (it was Maple EXP/hr). The program is now `Maple Classic Exp Overlay.exe`. Your log and settings carry over.
- **Class**: read from the status bar (never your character's name), shown in the log, and different classes get separate lines.
- **1-hour runs**: the overlay counts toward a full hour of non-stop farming on one map with the same party size, and saves each finished hour (these are what the best-maps site uses). A level-up doesn't stop a run; a break, a new map or a new party size starts it over.
- **Best maps for your class**: if you agree (it asks once), finished 1-hour runs are shared anonymously with the new stats site, maple-classic-exp-overlay.pages.dev, which shows the best maps for each class and path (named after its 4th job, like Night Lord or Bishop), level and party size. You can change this in Settings.
- **Sealed log**: every line the overlay writes is sealed, so lines edited or added by hand are greyed out, and an edited 1-hour run is never shared. Deleting a line, or renaming a map in the overlay, keeps everything else trusted.

**Fixed**
- The update window showed names with underscores wrong (e.g. explog.txt).

## 0.9.2-beta

**New**
- **Time** in the log: how long each line is based on, growing by 5 minutes with every update. Also in copied lines and `exp_log.txt`.
- **Report a bug** button (in Settings and in View capture): opens a bug report on GitHub, already filled in with what the overlay was reading, plus a picture to drag in. Nothing is sent until you click Submit.
- **Reset** now also looks for the minimap, inventory and party list again, so it picks them up after you move them.

**Fixed**
- The map name wasn't read when the overlay's window was right next to the minimap (but not covering it). The same is fixed for the party list.

## 0.9.1-beta

**New**
- **Pauses by itself** when you get no EXP for 15 seconds (you can change this in Settings) or leave the map, and carries on at your next EXP gain. Paused time doesn't count toward EXP/hr. On a new map, the numbers start from zero.
- **Don't log** button: keeps the current run out of the log while still showing everything. It turns itself off when the run ends.
- **Update check:** tells you when a new version is out and what's new, so you can decide whether to update. You can turn it off, or check by hand, in Settings.
- Your level shows within seconds of opening, and the time to your next level sits right under EXP/hr, like "Lv 31 in 36 min".
- The log's columns can be resized, and the window can be made bigger while the log is open. Both are remembered.

**Fixed**
- In a 1920x1080 window, the EXP number could be read with its first digits cut off.
- Mesos weren't read when the overlay's window was close to (but not covering) the inventory, or when a see-through inventory was in front of a busy background.

**More reliable**
- Much stronger protection against misread numbers, tested with thousands of simulated misreads and screenshots over many backgrounds: a misread can't pass for a level-up, a lost level, or extra mesos.
- Clearer status messages. "Select area" and "Follow game" are now one button.
- Safer saving: a damaged log file is backed up before anything is saved over it, and damaged settings fall back to the defaults.

## 0.9.0-beta

First public beta.

- Shows your EXP per hour, % per hour, EXP gained, and time to your next level.
- Finds the MapleStory window by itself, at any window size, windowed or fullscreen.
- Reads your mesos from the inventory, even while it's see-through during fights, and shows mesos per hour and per 5 minutes.
- Notices whether you're solo or in a party, and how many people are in it.
- Reads the map name from the minimap.
- Keeps a log of your EXP/hr and mesos/hr for each day, map, level and party size, updated every 5 minutes of non-stop farming.
- Settings for the break length, window transparency and always-on-top.
- Saves your settings and log in `%APPDATA%\MapleEXP`, and writes problems to `errors.log` there.

# Changelog

## 0.9.1-beta

**New**
- **Pauses by itself** when you get no EXP for 15 seconds (you can change this in Settings) or leave the map, and carries on at your next EXP gain. Paused time doesn't count toward EXP/hr. On a new map, the numbers start from zero.
- **Don't log** button: keeps the current run out of the log while still showing everything. It turns itself off when the run ends.
- **Update check:** tells you when a new version is out and what's new, so you can decide whether to update. You can turn it off, or check by hand, in Settings.
- Your level shows within seconds of opening, and the time to your next level sits right under EXP/hr, like "Lv 31 in 36 min".
- The log's columns can be resized, and the window can be made bigger while the log is open. Both are remembered.

**Fixed**
- In a 1920x1080 window, the EXP number could be read with its first digits cut off.
- Mesos weren't read when the tracker's window was close to (but not covering) the inventory, or when a see-through inventory was in front of a busy background.

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

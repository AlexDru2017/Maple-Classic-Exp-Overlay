"""
MapleStory Classic World - EXP/hr tracker.

Finds the MapleStory window, locates the "EXP. 12,345 [12.34%]" text in its status bar at
any window size, reads the digits by matching them against examples of the game's font
(Tesseract OCR as a fallback), and calculates EXP per hour, %/hr, and time to next level.

While tracking it also reads the map name from the minimap, your mesos from the inventory
and your party size from the party list, and keeps a log of EXP/hr and mesos/hr per day,
map, level and party size.

It only looks at the screen: it never reads or changes the game's files or memory and
never sends input to the game. Your settings and log are kept in the MapleEXP folder
inside %APPDATA%.
"""
import ctypes
import json
import os
import queue
import re
import hashlib
import shutil
import sys
import threading
import time
import tkinter as tk
import traceback
from collections import Counter, deque, namedtuple
from ctypes import wintypes
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import font as tkfont, messagebox, simpledialog, ttk

import cv2
import mss
import numpy as np
import pytesseract
from PIL import Image, ImageDraw, ImageTk

# Use physical pixels so window positions, tkinter and mss screenshots all agree on
# scaled displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

VERSION = "0.9.0-beta"
WINDOW_TITLE = f"Maple EXP/hr  v{VERSION}"

# Files that come with the program (inside the .exe when packaged), the folder it runs from,
# and the folder for your settings, log and learned digits (MAPLE_EXP_DATA can override it).
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS = APP_DIR / "assets"
HOME_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MAPLE_EXP_DATA") or Path(os.environ.get("APPDATA") or Path.home()) / "MapleEXP")

CONFIG_PATH = DATA_DIR / "config.json"
ERROR_LOG_PATH = DATA_DIR / "errors.log"
TEMPLATE_PATH = ASSETS / "digits.npz"
ICON_PATH = ASSETS / "icon.ico"
POLL_SECONDS = 1.5          # how often to read the EXP bar
ROLLING_WINDOW = 300        # seconds used for the "last 5 min" rate
SEARCH_STRIP = 0.22         # bottom part of the game window searched for the EXP text
LOG_PATH = DATA_DIR / "exp_log.json"
LOG_TEXT_PATH = DATA_DIR / "exp_log.txt"
LOG_BLOCK = 300             # seconds of non-stop EXP gain behind each log update
MESOS_PATH = DATA_DIR / "mesos_digits.npz"             # learned as you play...
MESOS_START_PATH = ASSETS / "mesos_digits.npz"          # ...starting from these
MESOS_BOX_PATH = ASSETS / "mesos_box.npz"
SCAN_SECONDS = 4            # how often the whole window is checked for the inventory and party
DEFAULTS = {"gap_seconds": 15, "transparency": 0, "topmost": True}

# EXP needed to finish each level in MapleStory Classic World (index 0 = level 1),
# from meowdb.com/msclassic/guides/exp-table-level-1-to-100.
EXP_TABLE = [
    15, 34, 57, 92, 135, 372, 560, 840, 1242, 1716,
    2360, 3216, 4200, 5460, 7050, 8840, 11040, 13716, 16680, 20216,
    24402, 28980, 34320, 40512, 47216, 54900, 63666, 73080, 83720, 95700,
    108480, 122760, 138666, 155540, 174216, 194832, 216600, 240500, 266682, 294216,
    324240, 356916, 391160, 428280, 468450, 510420, 555680, 604416, 655200, 709716,
    748608, 789631, 832902, 878545, 926689, 977471, 1031036, 1087536, 1147132, 1209994,
    1276301, 1346242, 1420016, 1497832, 1579913, 1666492, 1757815, 1854143, 1955750, 2062925,
    2175973, 2295216, 2420993, 2553663, 2693603, 2841212, 2996910, 3161140, 3334370, 3517093,
    3709829, 3913127, 4127566, 4353756, 4592341, 4844001, 5109452, 5389449, 5684790, 11938059,
    25069923, 52646838, 110558359, 232172553, 487562361, 1023880958, 2150150011, 4515315023, 9482161548,
]

tesseract_ok = False        # set in main(); Tesseract is only a fallback


# --------------------------------------------------------------------------- files

_error_lock = threading.Lock()
_last_errors = {}


def log_error(what, exc=None):
    """Adds an error, with details, to errors.log in the data folder (the same one at most
    once a minute), so players can send it in when something goes wrong."""
    key = (what, repr(exc))
    with _error_lock:
        now = time.monotonic()
        if now - _last_errors.get(key, -1e9) < 60:
            return
        _last_errors[key] = now
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if ERROR_LOG_PATH.exists() and ERROR_LOG_PATH.stat().st_size > 1_000_000:
            os.replace(ERROR_LOG_PATH, ERROR_LOG_PATH.with_suffix(".old.log"))
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] v{VERSION}: {what}\n{details}\n")
    except Exception:
        pass


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_config(cfg):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        log_error("saving settings", e)


def move_old_data():
    """Earlier versions kept settings and the log next to the program: copy them over once."""
    for name in ("config.json", "exp_log.json", "exp_log.txt", "mesos_digits.npz"):
        old, new = HOME_DIR / name, DATA_DIR / name
        if old.exists() and not new.exists():
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
            except Exception as e:
                log_error(f"copying {name} from the program's folder", e)


def find_tesseract():
    candidates = [
        HOME_DIR / "tesseract" / "tesseract.exe",            # comes with the download
        APP_DIR / "tesseract" / "tesseract.exe",
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return str(path)
    return None


# --------------------------------------------------------------------------- game window

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _api(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, list(argtypes)
    return fn


_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_EnumWindows = _api(_user32.EnumWindows, wintypes.BOOL, _EnumWindowsProc, wintypes.LPARAM)
_IsWindow = _api(_user32.IsWindow, wintypes.BOOL, wintypes.HWND)
_IsWindowVisible = _api(_user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_IsIconic = _api(_user32.IsIconic, wintypes.BOOL, wintypes.HWND)
_GetClassName = _api(_user32.GetClassNameW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowText = _api(_user32.GetWindowTextW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetClientRect = _api(_user32.GetClientRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_ClientToScreen = _api(_user32.ClientToScreen, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
_GetWindowThreadProcessId = _api(_user32.GetWindowThreadProcessId, wintypes.DWORD,
                                 wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_OpenProcess = _api(_kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_QueryFullProcessImageName = _api(_kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE,
                                  wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_CloseHandle = _api(_kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
_GetAncestor = _api(_user32.GetAncestor, wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetWindowRect = _api(_user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_FindWindow = _api(_user32.FindWindowW, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
_ShowWindow = _api(_user32.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_SetForegroundWindow = _api(_user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_CreateMutex = _api(_kernel32.CreateMutexW, wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)


def _process_name(hwnd):
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        ok = _QueryFullProcessImageName(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower() if ok else ""
    finally:
        _CloseHandle(handle)


def client_rect(hwnd):
    """(left, top, width, height) of the window's game area in screen pixels, or None."""
    if not _IsWindow(hwnd) or _IsIconic(hwnd):
        return None
    r, pt = wintypes.RECT(), wintypes.POINT(0, 0)
    if not _GetClientRect(hwnd, ctypes.byref(r)) or not _ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    if r.right < 100 or r.bottom < 100:
        return None
    return pt.x, pt.y, r.right, r.bottom


def find_game_window():
    """Handle of the MapleStory game window (the biggest, if there are several), or None."""
    found = []

    def check(hwnd, _):
        if _IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            title = ctypes.create_unicode_buffer(256)
            _GetClassName(hwnd, cls, 256)
            _GetWindowText(hwnd, title, 256)
            if cls.value == "MapleStoryClass" or (
                    "maple" in title.value.lower() and _process_name(hwnd) == "maplestory.exe"):
                rect = client_rect(hwnd)
                found.append((rect[2] * rect[3] if rect else 0, hwnd))
        return True

    _EnumWindows(_EnumWindowsProc(check), 0)
    return max(found)[1] if found else None


def grab(sct, left, top, width, height):
    """Screenshot of a screen rectangle, clipped to the monitors: (bgr, left, top) or None."""
    v = sct.monitors[0]
    x0, y0 = max(left, v["left"]), max(top, v["top"])
    x1 = min(left + width, v["left"] + v["width"])
    y1 = min(top + height, v["top"] + v["height"])
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    shot = np.array(sct.grab({"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}))
    return np.ascontiguousarray(shot[:, :, :3]), x0, y0


# --------------------------------------------------------------------------- reading the bar
#
# The status bar shows "EXP. 38,849 [61.02%]": white digits and green brackets. The game
# draws it in a small pixel font scaled up to the window size, so once a digit is rescaled
# to a standard height it can be recognised by comparing it pixel by pixel with saved
# examples of each digit (digits.npz).

Blob = namedtuple("Blob", "i x y w h")
# cells are digit positions (centre x, width, "digit" or "wide"); V is the brightness image
Layout = namedtuple("Layout", "gh cy exp pct box V")

CANON = 24                  # digits are compared at this height
PH, PW = 28, 19             # size of one digit patch at that height
SHIFT = 3                   # px of wiggle room when lining a digit up with an example
MIN_SCORE = 0.75            # correlation the best digit needs (true matches score ~0.9+)
MIN_LEAD = 0.03             # ...and how far ahead of the runner-up it must be


def _blobs(mask):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return labels, [Blob(i, *map(int, stats[i][:4])) for i in range(1, n)]


def _left_heavy(labels, b):
    """True for '[' (stroke on the left), False for ']'."""
    m = labels[b.y:b.y + b.h, b.x:b.x + b.w] == b.i
    half = max(1, b.w // 2)
    return m[:, :half].sum() > m[:, b.w - half:].sum()


def _cells(blobs, gh):
    """Digit positions. A blob as wide as several digits (touching digits) is split evenly."""
    pitch = 0.84 * gh
    out = []
    for b in blobs:
        if 0.95 * b.h < b.w < 1.25 * gh:
            out.append((b.x + b.w / 2, b.w, "wide"))          # the '%' sign
            continue
        n = max(1, int(round((b.w + 0.1 * gh) / pitch)))
        out.extend((b.x + (k + 0.5) * b.w / n, b.w / n, "digit") for k in range(n))
    return out


def find_exp(bgr):
    """Locate the EXP text in a screenshot. Returns a Layout or None."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    green = (sat >= 90) & (val >= 110) & (hue >= 26) & (hue <= 50)
    white = (sat < 70) & (val >= 140)
    glabels, gblobs = _blobs(green)
    _, wblobs = _blobs(white)
    brackets = sorted((b for b in gblobs if b.h >= 5 and b.w <= 0.6 * b.h), key=lambda b: b.x)
    V = val.astype(np.float32)
    found = []
    for o in brackets:
        bh, cy = o.h, o.y + o.h / 2
        on_line = lambda b: abs(b.y + b.h / 2 - cy) <= 0.3 * bh
        if o.w >= 6 and not _left_heavy(glabels, o):
            continue                                          # clearly a ']'
        right = [c for c in brackets if c.x > o.x + o.w and c.x - o.x <= 9 * bh
                 and on_line(c) and abs(c.h - bh) <= 0.25 * bh]
        if not right:
            continue
        c = right[0]
        if c.w >= 6 and _left_heavy(glabels, c):
            continue                                          # clearly another '['
        line = [b for b in wblobs if on_line(b) and b.h >= 0.6 * bh]
        inside = sorted((b for b in line if b.x >= o.x + o.w and b.x + b.w <= c.x), key=lambda b: b.x)
        if len(inside) < 2 or any(b.h > 1.25 * bh for b in inside):
            continue
        # The EXP number ends right before '['; walk left until a gap or the taller label.
        run, edge, dh = [], o.x, None
        for b in sorted((b for b in line if b.x + b.w <= o.x), key=lambda b: -b.x):
            if edge - (b.x + b.w) > 2.0 * bh or b.h > 1.25 * bh or (dh and b.h > 1.15 * dh):
                break
            run.append(b)
            edge, dh = b.x, dh or b.h
        if not run:
            continue                                          # HP/MP: label before '['
        run.reverse()
        heights = [b.h for b in run + inside if b.w <= 0.85 * b.h]
        gh = float(np.median(heights)) if heights else float(bh)
        found.append(Layout(gh, cy, _cells(run, gh), _cells(inside, gh),
                            (run[0].x, int(cy - bh), c.x + c.w, int(cy + bh)), V))
    if not found:
        return None
    # The status bar is the lowest line found; EXP is the right-most value on it.
    low = max(f.cy for f in found)
    return max((f for f in found if f.cy >= low - 0.5 * f.gh), key=lambda f: f.box[0])


def digit_patch(V, cx, cy, gh, shift):
    """Brightness patch around one digit, rescaled so the digit is CANON px tall."""
    k = gh / CANON
    x0 = cx - (PW / 2 + shift) * k
    y0 = cy - (PH / 2 + shift) * k
    M = np.float32([[1 / k, 0, -x0 / k], [0, 1 / k, -y0 / k]])
    return cv2.warpAffine(V, M, (PW + 2 * shift, PH + 2 * shift), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _unit_rows(a):
    a = a - a.mean(axis=1, keepdims=True)
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-6)


class DigitTemplates:
    """Example patches of each digit; scores a patch against all of them at once."""

    def __init__(self, examples):
        labels, rows = [], []
        for digit, patches in sorted(examples.items()):
            for p in patches:
                labels.append(digit)
                rows.append(np.asarray(p, np.float32).reshape(-1))
        self.labels = np.array(labels)
        self.digits = sorted(set(labels))
        self.mat = _unit_rows(np.stack(rows))

    @classmethod
    def load(cls, path=TEMPLATE_PATH):
        try:
            data = np.load(path)
            if int(data["canon"]) != CANON:
                return None
            return cls({k[1:]: data[k] for k in data.files if k.startswith("d")})
        except Exception:
            return None

    def scores(self, patch):
        """Best correlation per digit, trying every small shift of the patch."""
        windows = np.lib.stride_tricks.sliding_window_view(patch, (PH, PW)).reshape(-1, PH * PW)
        s = (_unit_rows(windows) @ self.mat.T).max(axis=0)
        return {d: float(s[self.labels == d].max()) for d in self.digits}

    def match(self, lay, cell):
        """(digit, score, lead over the runner-up) for one digit position."""
        best = {}
        for f in (0.95, 1.0, 1.05):         # allow for small errors in the size estimate
            for d, s in self.scores(digit_patch(lay.V, cell[0], lay.cy, lay.gh * f, SHIFT)).items():
                best[d] = max(best.get(d, -1.0), s)
        ranked = sorted(best.values(), reverse=True)
        lead = ranked[0] - ranked[1] if len(ranked) > 1 else 1.0
        return max(best, key=best.get), ranked[0], lead


def _pct_cells(lay):
    """The digits inside the brackets: drops the '%' sign right before the ']'."""
    return [c for c in lay.pct if lay.box[2] - c[0] > 1.5 * lay.gh]


def _read_digits(tpl, lay, cells):
    s = ""
    for cell in cells:
        if cell[2] != "digit":
            return None
        digit, score, lead = tpl.match(lay, cell)
        if score < MIN_SCORE or lead < MIN_LEAD:
            return None
        s += digit
    return s


def read_layout(tpl, lay):
    """(exp, pct) read with the digit templates, or None if any digit is unclear."""
    exp_s = _read_digits(tpl, lay, lay.exp)
    pct_s = _read_digits(tpl, lay, _pct_cells(lay))
    if not exp_s or not pct_s or not 3 <= len(pct_s) <= 5:
        return None
    if (len(exp_s) > 1 and exp_s[0] == "0") or (len(pct_s) > 3 and pct_s[0] == "0"):
        return None                                # a leading 0: some digits were cut off
    return int(exp_s), int(pct_s) / 100          # the game always shows 2 decimals


def _ocr_cells(bgr, lay, cells):
    """Tesseract on the area covering cells, two ways. Returns digit strings."""
    if not cells:
        return []
    g = lay.gh
    x0 = int(min(c[0] - c[1] / 2 for c in cells) - 0.1 * g)
    x1 = int(max(c[0] + c[1] / 2 for c in cells) + 0.1 * g)
    crop = bgr[max(0, int(lay.cy - 0.75 * g)):int(lay.cy + 0.75 * g), max(0, x0):x1]
    if crop.size == 0:
        return []
    big = cv2.resize(crop, None, fx=40 / g, fy=40 / g, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    white = (((hsv[..., 1] < 70) & (hsv[..., 2] >= 140)) * 255).astype(np.uint8)
    white = cv2.dilate(white, np.ones((3, 3), np.uint8))
    _, bright = cv2.threshold(hsv[..., 2], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out = []
    for mask in (white, bright):
        img = cv2.copyMakeBorder(255 - mask, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        text = pytesseract.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=0123456789,.")
        out.append(re.sub(r"\D", "", text))
    return out


def tesseract_candidates(bgr, lay):
    """Fallback: (exp, pct) guesses from Tesseract. The tracker picks one that adds up."""
    if not tesseract_ok:
        return []
    exps = {int(s) for s in _ocr_cells(bgr, lay, lay.exp) if s}
    pcts = {int(s) / 100 for s in _ocr_cells(bgr, lay, _pct_cells(lay))
            if 3 <= len(s) <= 5}
    return [(e, p) for e in sorted(exps) for p in sorted(pcts)]


def build_templates(samples, scales=(1.0, 0.75, 0.55, 0.4, 0.3), path=TEMPLATE_PATH):
    """Make digits.npz from screenshots of the bar: [(bgr, "38,849", "61.02"), ...].

    Each screenshot is also shrunk to several sizes so small windows are covered too.
    """
    examples = {}
    for bgr, exp_s, pct_s in samples:
        for f in scales:
            img = bgr if f == 1.0 else cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            lay = find_exp(img)
            if not lay:
                continue
            exp_cells, pct_cells = lay.exp, _pct_cells(lay)
            exp_t, pct_t = re.sub(r"\D", "", exp_s), re.sub(r"\D", "", pct_s)
            if len(exp_cells) != len(exp_t) or len(pct_cells) != len(pct_t):
                continue
            for cell, ch in zip(exp_cells + pct_cells, exp_t + pct_t):
                examples.setdefault(ch, []).append(digit_patch(lay.V, cell[0], lay.cy, lay.gh, 0))
    np.savez(path, canon=CANON, **{f"d{k}": np.stack(v) for k, v in examples.items()})
    return {k: len(v) for k, v in sorted(examples.items())}


# --------------------------------------------------------------------------- map name and level
#
# The minimap's light-blue info box shows the region and, under it, the map name in white
# text. It's read with Tesseract a few different ways, and the most common answer wins.
# The level comes from the EXP table: each level needs a different amount of EXP.

def level_for_req(req):
    """The level whose EXP requirement matches req, from the Classic World EXP table."""
    if not req:
        return None
    for level, need in enumerate(EXP_TABLE, start=1):
        if abs(req - need) <= 0.005 * need:
            return level
    return None


def _whiteness(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    return np.clip((hsv[..., 2] - 150) / 90, 0, 1) * np.clip((80 - hsv[..., 1]) / 50, 0, 1)


def _ocr_votes(whiteness, glyph_h, config, clean):
    """Tesseract on white text at a few sizes, with and without smoothing. Counts answers."""
    votes = Counter()
    for target in (36, 48, 60):
        s = target / glyph_h
        big = cv2.resize(whiteness, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        for img in ((big > 0.5) * 255.0, np.clip(big, 0, 1) * 255):
            img = cv2.copyMakeBorder((255 - img).astype(np.uint8), 24, 24, 24, 24,
                                     cv2.BORDER_CONSTANT, value=255)
            text = clean(pytesseract.image_to_string(img, config=config))
            if text:
                votes[text] += 1
    return votes


def find_map_box(bgr, gh):
    """(x, y, w, h) of the minimap's info box in a shot of the window's top-left, or None."""
    hue, sat, val = [c.astype(np.int16) for c in cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))]
    box = (abs(hue - 100) <= 8) & (abs(sat - 64) <= 30) & (abs(val - 204) <= 30)
    n, _, stats, _ = cv2.connectedComponentsWithStats(box.astype(np.uint8), connectivity=8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = map(int, stats[i][:4])
    h = min(h, int(round(7.3 * gh)))            # just the info box, not the whole minimap
    return (x, y, w, h) if w >= 10 * gh and h >= 4 * gh else None


def _clean_map_name(text):
    t = text.replace("|", "I")                         # a lone "I" (as in "... I") reads as "|"
    t = re.sub(r"(?<=\s)[!\[\]](?=\s*$)", "I", t)
    t = re.sub(r"[^A-Za-z0-9 '<>:.&()\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .:-")
    t = re.sub(r"(?<= )[Il1]{3}$", "III", t)         # Roman numerals read as l's and 1's
    t = re.sub(r"(?<= )[Il1]{2}$", "II", t)
    t = re.sub(r"(?<= )l$", "I", t)
    return t if len(t) >= 3 else ""


def read_map_name(bgr, gh):
    """The map name (the lower line of white text) from a shot of the minimap's info box."""
    white = _whiteness(bgr[:, int(0.85 * bgr.shape[0]):])      # skip the map's icon
    _, blobs = _blobs(white > 0.5)
    glyphs = sorted((b for b in blobs if 0.3 * gh <= b.h <= 1.6 * gh and b.w <= 2.5 * gh),
                    key=lambda b: b.y + b.h / 2)
    lines = []
    for b in glyphs:
        last = lines[-1][-1] if lines else None
        if last and abs(b.y + b.h / 2 - (last.y + last.h / 2)) <= 0.5 * gh:
            lines[-1].append(b)
        else:
            lines.append([b])
    lines = [ln for ln in lines if len(ln) >= 3]
    if not lines:
        return None
    ln = lines[-1]
    x0, x1 = max(0, min(b.x for b in ln) - 2), max(b.x + b.w for b in ln) + 2
    y0, y1 = max(0, min(b.y for b in ln) - 2), max(b.y + b.h for b in ln) + 2
    votes = _ocr_votes(white[y0:y1, x0:x1], max(b.h for b in ln), "--psm 7", _clean_map_name)
    name, n = votes.most_common(1)[0] if votes else (None, 0)
    return name if n >= 2 else None


# --------------------------------------------------------------------------- mesos and party
#
# The inventory's mesos box (white, gold coin on its left) is found while it's solid, then
# read where it is, even while the window is see-through during fights: the digits stay
# about as much darker than the box as when it's solid. The digits are a different font
# from the EXP bar's, so examples of them are learned from your own screen
# (mesos_digits.npz), each checked by Tesseract first. Until all ten are learned the EXP
# font's digits are used, which match these closely enough.
# Party: the small party list shows one HP bar per member, in an evenly spaced column.

def find_mesos_field(bgr, gh=None):
    """(x, y, w, h) of the solid white mesos box (gold coin on its left), or None.

    With gh (the EXP digit height), the box must also be the mesos box's size at that scale:
    about 19.1 x 1.82 EXP digits."""
    hue, sat, val = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))
    n, _, stats, _ = cv2.connectedComponentsWithStats(((val > 225) & (sat < 30)).astype(np.uint8),
                                                      connectivity=8)
    coin = (hue >= 12) & (hue <= 35) & (sat >= 100) & (val >= 140)
    best = None
    for i in range(1, n):
        x, y, w, h, area = map(int, stats[i])
        if h < 8 or not 7 * h <= w <= 16 * h or area < 0.6 * w * h:
            continue
        if gh and not (16.2 * gh <= w <= 22 * gh and 1.45 * gh <= h <= 2.2 * gh):
            continue
        left = coin[y:y + h, max(0, x - int(1.6 * h)):x]
        if left.size == 0 or left.mean() < 0.15:
            continue
        if best is None or area > best[4]:
            best = (x, y, w, h, area)
    return best[:4] if best else None


def mesos_layout(bgr, field):
    """Digit cells of the number in the mesos box, or None if they can't be picked out cleanly.

    Works on how much darker each pixel is than the box right around it, so a see-through
    box over a changing background still works. Anything doubtful returns None: a wrong
    number is worse than waiting for a clean look.
    """
    x, y, w, h = field
    inner = bgr[y + 2:y + h - 2, x + 2:x + w - 2]
    if inner.size == 0:
        return None
    V = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    dh0 = 0.61 * h                                   # digits are ~61% as tall as the box
    k = max(3, int(round(0.35 * dh0)) | 1)
    strokes = cv2.morphologyEx(V, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) - V
    peak = float(np.percentile(strokes, 99.7))
    if peak < 40:
        return None
    band = np.zeros(strokes.shape, bool)             # the row the digits sit on
    mid = inner.shape[0] / 2
    b0, b1 = max(0, int(mid - 0.65 * dh0)), int(mid + 0.65 * dh0)
    band[b0:b1] = strokes[b0:b1] > 0.4 * peak
    c = max(1, int(round(0.06 * dh0)))
    band = cv2.morphologyEx(band.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((c, c), np.uint8)) > 0
    _, blobs = _blobs(band)
    digits = sorted((b for b in blobs if 0.6 * dh0 <= b.h <= 1.25 * dh0), key=lambda b: -b.x)
    if not digits or inner.shape[1] - (digits[0].x + digits[0].w) > 0.8 * dh0:
        return None                                  # the number is right-aligned
    run, edge = [], None
    for b in digits:
        if edge is not None and edge - (b.x + b.w) > 0.9 * dh0:
            break
        run.append(b)
        edge = b.x if edge is None else min(edge, b.x)
    run.reverse()
    dh = float(np.median([b.h for b in run]))
    pitch = 0.74 * dh
    cells = []
    for b in run:
        n = max(1, int(round((b.w + 0.12 * dh) / pitch)))
        cells.extend((b.x + (j + 0.5) * b.w / n, b.w / n, "digit") for j in range(n))
    # Nothing digit-like may be left over to the left of the number (a dropped digit).
    left = band[:, :max(0, int(cells[0][0] - cells[0][1] / 2 - 0.25 * dh))]
    if left.sum() > 0.25 * band[:, int(cells[-1][0] - cells[-1][1] / 2):].sum():
        return None
    # A comma after every 3 digits from the right makes that gap wider; anything else is off.
    centers = [c[0] for c in cells][::-1]
    for i in range(len(centers) - 1):
        want = 1.11 * dh if (i + 1) % 3 == 0 else 0.74 * dh
        if abs(centers[i] - centers[i + 1] - want) > 0.2 * dh:
            return None
    cy = float(np.median([b.y + b.h / 2 for b in run]))
    return Layout(dh, cy, cells, [], (0, 0, 0, 0), strokes)


# The game right-aligns the number: digit centres sit at these distances from the box's right
# edge, in box heights: first digit, each further digit, and extra room for every comma.
MESOS_FIRST, MESOS_PITCH, MESOS_COMMA = 0.36, 0.447, 0.30


def read_mesos_positions(bgr, field, templates, digit_count=None):
    """Reads the mesos box digit by digit at the spots the game draws them.

    Nothing has to be separated from the background first, so it also works when the
    inventory is see-through over a busy background. Every spot must clearly be a digit
    or clearly not one, and once the number ends the next few spots to its left must not
    look like digits, so a digit lost in a busy background can't silently shorten the
    number from the middle. If digit_count is given, the number must have exactly that many digits (which
    also catches a lost first digit). Returns (number, Layout, weakest score, weakest lead),
    or None.
    """
    x, y, w, h = field
    V = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    k = max(3, int(round(0.35 * 0.615 * h)) | 1)
    strokes = cv2.morphologyEx(V, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) - V
    lay = Layout(0.615 * h, y + 0.525 * h, [], [], (0, 0, 0, 0), strokes)
    digits, cells, scores, leads, ended = "", [], [], [], 0
    for i in range(16):
        cx = x + w - (MESOS_FIRST + i * MESOS_PITCH + (i // 3) * MESOS_COMMA) * h
        if cx - 0.3 * h < x:
            break
        digit, score, lead = templates.match(lay, (cx, 0.4 * h, "digit"))
        empty = bool(digits) and score < 0.65
        if ended:
            if not empty:
                return None                           # something digit-like after the end
            ended += 1
            if ended > 3:
                break
        elif score >= 0.75 and lead >= 0.05:          # clearly a digit
            digits, cells = digit + digits, [(cx, 0.4 * h, "digit")] + cells
            scores.append(score)
            leads.append(lead)
        elif empty:
            ended = 1                                 # the number ends here
        else:
            return None                               # unclear: don't guess
    if not digits or (len(digits) > 1 and digits[0] == "0"):
        return None
    if digit_count and len(digits) != digit_count:
        return None
    return int(digits), lay._replace(exp=cells), min(scores), min(leads)


def read_mesos_digits(lay, templates):
    """The number in the mesos box, or None if any digit is unclear."""
    s = ""
    for cell in lay.exp:
        digit, score, lead = templates.match(lay, cell)
        if score < 0.70 or lead < 0.04:
            return None
        s += digit
    return int(s)


def tesseract_mesos(lay):
    """(number, how many of 6 Tesseract tries agreed) for the mesos box, or (None, 0)."""
    if not tesseract_ok:
        return None, 0
    first, last = lay.exp[0], lay.exp[-1]
    x0, x1 = max(0, int(first[0] - first[1] / 2 - 0.5 * lay.gh)), int(last[0] + last[1] / 2 + 0.5 * lay.gh)
    y0, y1 = max(0, int(lay.cy - 0.8 * lay.gh)), int(lay.cy + 0.8 * lay.gh)
    img = np.clip(lay.V[y0:y1, x0:x1] / max(1.0, float(np.percentile(lay.V, 99.7))), 0, 1)

    def clean(text):
        return re.sub(r"\D", "", text) if re.fullmatch(r"[\d,.\s]+", text.strip() or "x") else ""

    votes = _ocr_votes(img.astype(np.float32), lay.gh,
                       "--psm 7 -c tessedit_char_whitelist=0123456789,", clean)
    digits, n = votes.most_common(1)[0] if votes else ("", 0)
    return (int(digits), n) if n >= 4 else (None, n)


class MesosDigits:
    """Examples of the mesos box's digits, learned from your own screen as you play."""

    PER_DIGIT = 12

    def __init__(self, path=MESOS_PATH, start=MESOS_START_PATH):
        self.path = path
        self.examples = {}
        self._templates = None
        for source in (path, start):                 # what you've learned, else what comes with it
            try:
                data = np.load(source)
                if int(data["canon"]) == CANON:
                    self.examples = {k[1:]: list(data[k]) for k in data.files if k.startswith("d")}
                    break
            except Exception:
                continue

    @property
    def complete(self):
        return all(str(d) in self.examples for d in range(10))

    def templates(self):
        if self._templates is None and self.examples:
            self._templates = DigitTemplates({d: np.stack(v) for d, v in self.examples.items()})
        return self._templates

    def learn(self, lay, value):
        digits = str(value)
        if len(lay.exp) != len(digits):
            return
        added = False
        for cell, ch in zip(lay.exp, digits):
            examples = self.examples.setdefault(ch, [])
            if len(examples) < self.PER_DIGIT:
                examples.append(digit_patch(lay.V, cell[0], lay.cy, lay.gh, 0))
                added = True
        if added:
            self._templates = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                np.savez(self.path, canon=CANON, **{f"d{k}": np.stack(v) for k, v in self.examples.items()})
            except Exception:
                pass


def _edges(bgr):
    v = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    return cv2.magnitude(cv2.Sobel(v, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(v, cv2.CV_32F, 0, 1, ksize=3))


class MesosBoxFinder:
    """Finds the mesos box even while the inventory is see-through.

    It looks for the outline of the whole row (gold coin, box, MESOS label) at the size the
    UI is drawn at, which stays visible when the window is see-through. The outline is kept
    in mesos_box.npz. Work is done at a small scale (EXP digits 12 px tall) to be quick.
    """

    GH = 12.0

    def __init__(self, path=MESOS_BOX_PATH):
        self.edges = None
        try:
            data = np.load(path)
            self.edges, self.mask = data["edges"], data["mask"]
            self.ox, self.oy, self.bw, self.bh = map(float, data["box"])
        except Exception:
            pass

    @classmethod
    def build(cls, bgr, field, gh, path=MESOS_BOX_PATH):
        """Make mesos_box.npz from a screenshot showing the solid box at `field`."""
        x, y, w, h = field
        x0, x1 = int(x - 1.45 * h), int(x + w + 2.7 * h)
        y0, y1 = int(y - 0.35 * h), int(y + 1.35 * h)
        s = cls.GH / gh
        edges = cv2.resize(_edges(bgr[y0:y1, x0:x1]), None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        mask = np.ones(edges.shape, np.float32)       # leave out the inside of the box
        mask[int((y - y0 + 0.12 * h) * s):int((y - y0 + 0.88 * h) * s),
             int((x - x0 + 0.12 * h) * s):int((x - x0 + w - 0.12 * h) * s)] = 0
        np.savez(path, edges=edges, mask=mask, box=np.float32([(x - x0) * s, (y - y0) * s, w * s, h * s]))

    def find(self, bgr, gh):
        """(x, y, w, h) of the best-matching mesos box in a screenshot, or None."""
        if self.edges is None:
            return None
        s = self.GH / gh
        edges = cv2.resize(_edges(bgr), None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if edges.shape[0] < self.edges.shape[0] or edges.shape[1] < self.edges.shape[1]:
            return None
        r = np.nan_to_num(cv2.matchTemplate(edges, self.edges, cv2.TM_CCORR_NORMED, mask=self.mask),
                          nan=0.0, posinf=0.0, neginf=0.0)
        r[np.abs(r) > 1.0] = 0.0                       # flat areas (e.g. all black) give junk values
        _, best, _, (px, py) = cv2.minMaxLoc(r)
        r[max(0, py - 3):py + 4, max(0, px - 12):px + 13] = 0
        _, second, _, _ = cv2.minMaxLoc(r)
        if best < 0.72 or best - second < 0.05:       # not clearly there
            return None
        return (int((px + self.ox) / s), int((py + self.oy) / s), int(self.bw / s), int(self.bh / s))


def _agrees(value, last):
    """Two mesos readings that could both be right: same number of digits and close together."""
    return last is not None and len(str(value)) == len(str(last)) and abs(value - last) <= max(1000, 0.02 * last)


def find_party_bars(bgr, gh):
    """HP bars of the party list's members: the longest evenly spaced column of identical
    HP-bar outlines. A single bar (or none) means you're on your own."""
    val = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2]
    n, _, stats, _ = cv2.connectedComponentsWithStats((val < 70).astype(np.uint8), connectivity=8)
    bars = sorted((tuple(map(int, stats[i][:4])) for i in range(1, n)
                   if 7.5 * gh <= stats[i][2] <= 10.5 * gh and 1.3 * gh <= stats[i][3] <= 1.85 * gh),
                  key=lambda b: b[1])
    best = []
    for i, first in enumerate(bars):
        same = [b for b in bars[i:] if abs(b[0] - first[0]) <= 0.5 * gh
                and abs(b[2] - first[2]) <= 0.5 * gh and abs(b[3] - first[3]) <= 0.3 * gh]
        column, pitch = [first], None
        for b in same[1:]:
            step = b[1] - column[-1][1]
            if pitch is None and 1.8 * gh <= step <= 3.0 * gh:
                pitch = step
            if pitch is None or abs(step - pitch) > 0.3 * gh:
                break
            column.append(b)
        if len(column) > len(best):
            best = column
    return best if len(best) >= 2 else best[:1]


# --------------------------------------------------------------------------- finding it on screen

Reading = namedtuple("Reading", "where candidates confident text image boxes map note "
                                 "mesos mesos_image mesos_boxes party mesos_at",
                     defaults=((), False, "", None, (), None, None, None, None, (), None, None))


def _view(img, lay):
    """Crop around the EXP text and the digit boxes in it, for View capture."""
    g = lay.gh
    x0, x1 = max(0, int(lay.box[0] - 2 * g)), min(img.shape[1], int(lay.box[2] + g))
    y0, y1 = max(0, int(lay.cy - 1.2 * g)), min(img.shape[0], int(lay.cy + 1.2 * g))
    boxes = [(c[0] - c[1] / 2 - x0, lay.cy - g / 2 - y0, c[0] + c[1] / 2 - x0, lay.cy + g / 2 - y0, kind)
             for kind, cells in (("exp", lay.exp), ("pct", _pct_cells(lay))) for c in cells]
    return img[y0:y1, x0:x1].copy(), boxes


class ScreenReader:
    """Finds the EXP text on screen and reads it, following the game window around.

    It also watches the minimap so the map name is read again whenever it changes, reads
    the inventory's mesos and, every few seconds, counts the members in the party list.
    """

    def __init__(self, templates, mode="window", region=None):
        self.templates = templates
        self.mode, self.region = mode, region
        self.hwnd = None
        self.size = None
        self.box = None             # where the EXP text is, relative to the game window
        self.misses = 0
        self.last_full_search = 0.0
        self.gh = None              # EXP digit height: how big the game's UI is drawn
        self.map_box = None         # the minimap's info box, relative to the game window
        self.map_print = None       # rough picture of its text, to notice map changes
        self.map_unread = False
        self.next_map_search = 0.0
        self.next_map_read = 0.0
        self.mesos_box = None       # the inventory's mesos box, relative to the game window
        self.saved_mesos = None     # (window w, h, x, y, w, h) remembered from last time
        self.box_finder = MesosBoxFinder()
        self.mesos_digits = MesosDigits()
        self.mesos_checked = None   # last mesos number Tesseract confirmed
        self.mesos_count = None     # how many digits the mesos number has
        self.mesos_pending = (None, 0)
        self.mesos_fails = 0
        self.next_mesos_check = 0.0
        self.party_area = None      # where the party list was last seen, on screen
        self.next_scan = 0.0
        self.own_rect = None        # this app's window, which screenshots can't see through

    def use_window(self):
        self.mode, self.hwnd, self.box = "window", None, None

    def use_region(self, region):
        self.mode, self.region = "region", region

    def request_map(self):
        """Read the map name on the next look, even if it doesn't seem to have changed."""
        self.next_map_search, self.next_map_read, self.map_unread = 0.0, 0.0, True

    def read(self, sct):
        rect = self._game_rect()
        if self.mode == "region":
            reading = self._read_region(sct, rect)
        elif not self.hwnd:
            return Reading("MapleStory window not found. Is the game running?")
        elif not rect:
            return Reading("MapleStory is minimized")
        else:
            reading = self._read_window(sct, rect)
        if rect and self.gh:
            name, note = self._read_info(sct, rect)
            reading = self._read_extras(sct, rect, reading._replace(map=name, note=note))
        return reading

    def _game_rect(self):
        if not (self.hwnd and _IsWindow(self.hwnd)):
            self.hwnd, self.box, self.map_box = find_game_window(), None, None
        rect = client_rect(self.hwnd) if self.hwnd else None
        if rect and rect[2:] != self.size:                  # resized: find everything again
            self.size, self.box, self.map_box, self.gh = rect[2:], None, None, None
            self.mesos_box, self.party_area, self.next_scan = None, None, 0.0
            saved = self.saved_mesos
            if saved and tuple(saved[:2]) == tuple(rect[2:]):
                self.mesos_box = tuple(saved[2:])           # where it was last time
        return rect

    def _hide_own(self, shot):
        """The screenshot with this app's window blacked out, so it can never read itself."""
        r = self.own_rect
        if not shot or not r:
            return shot
        img, sx, sy = shot
        x0, y0 = max(0, r[0] - sx), max(0, r[1] - sy)
        x1, y1 = min(img.shape[1], r[0] + r[2] - sx), min(img.shape[0], r[1] + r[3] - sy)
        if x0 < x1 and y0 < y1:
            img = img.copy()
            img[y0:y1, x0:x1] = 0
        return img, sx, sy

    def _covered(self, x, y, w, h, margin=0):
        """Does this app's window overlap (or come within margin px of) the given area?"""
        r = self.own_rect
        return bool(r) and (x - margin < r[0] + r[2] and r[0] < x + w + margin
                            and y - margin < r[1] + r[3] and r[1] < y + h + margin)

    def _remember(self, lay, rect):
        if lay and rect:
            self.gh = lay.gh                # tells the minimap reader how big the UI is

    def _read_region(self, sct, rect):
        r = self.region
        if not r:
            return Reading("No area selected")
        shot = self._hide_own(grab(sct, r["left"], r["top"], r["width"], r["height"]))
        reading, lay = self._read(shot, "Selected area")
        if reading.confident:
            self._remember(lay, rect)
        return reading

    def _read_window(self, sct, rect):
        wx, wy, ww, wh = rect
        where = f"MapleStory window {ww}x{wh}"
        if self.box:
            bx, by, bw, bh = self.box
            if self._covered(wx + bx, wy + by, bw, bh):
                return Reading(f"{where}: this window is covering the EXP bar. Move it.")
            shot = grab(sct, wx + bx, wy + by, bw, bh)
            reading, lay = self._read(shot, where)
            if reading.confident:
                self.misses = 0
                self._remember(lay, rect)
                return reading
            # Not reading cleanly here (a dialog in the way, or this was the wrong spot):
            # after a few tries, look for the EXP text again.
            self.misses += 1
            if self.misses < 3:
                return reading
            self.box = None

        # Search the bottom of the window (and now and then the whole window).
        top = int(wh * (1 - SEARCH_STRIP))
        shot = self._hide_own(grab(sct, wx, wy + top, ww, wh - top))
        lay = find_exp(shot[0]) if shot else None
        if not lay and time.monotonic() - self.last_full_search > 10:
            self.last_full_search = time.monotonic()
            shot = self._hide_own(grab(sct, wx, wy, ww, wh))
            lay = find_exp(shot[0]) if shot else None
        if not lay:
            if self._covered(wx, wy + top, ww, wh - top):
                return Reading(f"{where}: can't find the EXP text. Is this window covering it?")
            return Reading(f"{where}: can't find the EXP text")
        img, sx, sy = shot
        g = lay.gh
        x0, x1 = max(0, int(lay.box[0] - g)), min(img.shape[1], int(lay.box[2] + 6 * g))
        y0, y1 = max(0, int(lay.cy - 1.4 * g)), min(img.shape[0], int(lay.cy + 1.4 * g))
        reading, lay = self._read(shot, where, lay)
        if reading.confident:             # only remember a spot whose digits match the game font
            self.box = (sx - wx + x0, sy - wy + y0, x1 - x0, y1 - y0)
            self.misses = 0
            self._remember(lay, rect)
        return reading

    def _read(self, shot, where, lay=None):
        """Returns (Reading, Layout or None)."""
        if not shot:
            return Reading(f"{where}: off screen"), None
        img = shot[0]
        lay = lay or find_exp(img)
        if not lay:
            return Reading(f"{where}: can't find the EXP text"), None
        view, boxes = _view(img, lay)
        got = read_layout(self.templates, lay) if self.templates else None
        if got:
            return Reading(where, [got], True, f"{got[0]:,} [{got[1]:.2f}%]", view, boxes), lay
        cands = tesseract_candidates(img, lay)
        if cands:
            e, p = cands[0]
            return Reading(where, cands, False, f"{e:,} [{p:.2f}%] (Tesseract)", view, boxes), lay
        return Reading(f"{where}: found the EXP text but can't read the digits",
                       image=view, boxes=boxes), lay

    def _read_extras(self, sct, rect, reading):
        """Adds the mesos count and, every few seconds, the party size to a reading."""
        wx, wy, ww, wh = rect
        now, g = time.monotonic(), self.gh
        notes = [reading.note] if reading.note else []
        party = moved = None
        if now >= self.next_scan:                     # look over the whole window
            self.next_scan = now + (SCAN_SECONDS if self.mesos_box else 2)
            shot = self._hide_own(grab(sct, wx, wy, ww, wh))
            if shot:
                img, sx, sy = shot
                field = find_mesos_field(img, g)      # the solid box is easy to spot
                if field is None and (self.mesos_box is None or self.mesos_fails >= 10):
                    field = self._find_seethrough_box(img, g)
                if field:
                    box = (sx - wx + field[0], sy - wy + field[1], field[2], field[3])
                    old = self.mesos_box
                    if old is None or max(abs(a - b) for a, b in zip(box, old)) > 3:
                        self.mesos_box, self.mesos_fails = box, 0
                        moved = (ww, wh) + box
                area = self.party_area
                if area and self._covered(*area):
                    notes.append("This window is covering the party list.")
                else:
                    bars = find_party_bars(img, g)
                    party = max(1, len(bars))
                    if len(bars) >= 2:
                        x0, y0 = min(b[0] for b in bars), min(b[1] for b in bars)
                        x1, y1 = max(b[0] + b[2] for b in bars), max(b[1] + b[3] for b in bars)
                        self.party_area = (sx + x0 - int(12 * g), sy + y0 - int(g),
                                           x1 - x0 + int(13 * g), y1 - y0 + int(2 * g))
        mesos, image, boxes = None, None, ()
        if self.mesos_box:
            bx, by, bw, bh = self.mesos_box
            if self._covered(wx + bx, wy + by, bw, bh, margin=bh):
                notes.append("This window is covering the inventory's mesos.")
            else:
                mesos, image, boxes = self._read_mesos(sct, wx + bx, wy + by, bw, bh)
        return reading._replace(mesos=mesos, mesos_image=image, mesos_boxes=boxes, party=party,
                                mesos_at=moved, note=" ".join(notes) or None)

    def _find_seethrough_box(self, img, g):
        """The mesos box by its outline, checked by reading clear digits there."""
        field = self.box_finder.find(img, g)
        if not field:
            return None
        learned = self.mesos_digits
        templates = learned.templates() if learned.complete else self.templates
        return field if templates and read_mesos_positions(img, field, templates) else None

    def _read_mesos(self, sct, x, y, w, h):
        """Reads the mesos box at (x, y, w, h) on screen. Returns (number, image, boxes)."""
        shot = grab(sct, x - 2 * h, y - h, w + 4 * h, 3 * h)
        if not shot:
            return None, None, ()
        img, sx, sy = shot
        fx, fy = x - sx, y - sy
        inner = cv2.cvtColor(img[fy + 2:fy + h - 2, fx + 2:fx + w - 2], cv2.COLOR_BGR2HSV)
        solid = ((inner[..., 2] > 225) & (inner[..., 1] < 30)).mean() > 0.6

        def boxes_of(lay, ox=0, oy=0):
            return [(ox + c[0] - c[1] / 2, oy + lay.cy - lay.gh / 2,
                     ox + c[0] + c[1] / 2, oy + lay.cy + lay.gh / 2, "mesos") for c in lay.exp]

        learned = self.mesos_digits
        if learned.complete:                          # all ten mesos digits learned
            got = read_mesos_positions(img, (fx, fy, w, h), learned.templates())
            if not got:
                self.mesos_fails += 1
                return None, img, ()
            self.mesos_fails = 0
            value, lay, score, lead = got
            if not solid and len(str(value)) != self.mesos_count:
                # The first reading, or a different number of digits than before (that only
                # happens when crossing 10,000, 100,000...): on a see-through box, only
                # believe it once 3 readings in a row agree.
                last, n = self.mesos_pending
                self.mesos_pending = (value, n + 1 if _agrees(value, last) else 1)
                if self.mesos_pending[1] < 3:
                    return None, img, boxes_of(lay)
            self.mesos_count, self.mesos_pending = len(str(value)), (None, 0)
            if solid and score >= 0.95 and lead >= 0.15:
                learned.learn(lay, value)             # more examples, from very clean looks
            return value, img, boxes_of(lay)
        # Still learning the mesos font: a new number needs Tesseract's OK. On a solid box
        # Tesseract alone is enough; on a see-through box both readers must agree.
        lay = mesos_layout(img, (fx, fy, w, h))
        if not lay:
            self.mesos_fails += 1
            return None, img, ()
        self.mesos_fails = 0
        boxes = boxes_of(lay, fx + 2, fy + 2)
        value = read_mesos_digits(lay, self.templates) if self.templates else None
        if value is not None and value == self.mesos_checked:
            return value, img, boxes
        now = time.monotonic()
        if now < self.next_mesos_check or (not solid and value is None):
            return None, img, boxes
        self.next_mesos_check = now + (2 if value is not None else 5)
        checked, votes = tesseract_mesos(lay)
        if checked is None or (not solid and checked != value):
            return None, img, boxes
        self.mesos_checked = checked
        if checked == value or votes == 6:            # two readers agree: learn these digits
            learned.learn(lay, checked)
        return checked, img, boxes

    def _read_info(self, sct, rect):
        """Returns (map name if it was just read, note)."""
        if not tesseract_ok:
            return None, "Map names can't be read: Tesseract OCR is missing (see the README)."
        if self.own_rect is None:
            return None, None                   # wait until we know where this app's window is
        wx, wy, ww, wh = rect
        g, now = self.gh, time.monotonic()
        name = note = None

        if self.map_box is None and now >= self.next_map_search:
            self.next_map_search = now + 3
            shot = self._hide_own(grab(sct, wx, wy, int(ww * 0.4), int(wh * 0.3)))
            box = find_map_box(shot[0], g) if shot else None
            if box:
                self.map_box = (shot[1] - wx + box[0], shot[2] - wy + box[1], box[2], box[3])
                self.map_print, self.map_unread = None, True
        if self.map_box is None:
            if self._covered(wx, wy, int(ww * 0.4), int(wh * 0.3)):
                note = "This window may be covering the minimap. Move it so the map name can be read."
            else:
                note = "Can't see the map name. Is the minimap open?"
        else:
            bx, by, bw, bh = self.map_box
            if self._covered(wx + bx, wy + by, bw, bh, margin=g):
                # Even a box that only touches this window may be cut off by it. Find the
                # whole box again (and read the name) once this window is out of the way.
                note = "This window is covering the minimap. Move it so the map name can be read."
                self.map_box, self.map_unread = None, True
            else:
                shot = grab(sct, wx + bx, wy + by, bw, bh)
                if shot:
                    picture = cv2.resize(_whiteness(shot[0]), (64, 16), interpolation=cv2.INTER_AREA) > 0.3
                    if self.map_print is not None and (picture != self.map_print).mean() > 0.04:
                        # Changed map: find the box again (its size can change), then read it.
                        self.map_box, self.next_map_search = None, now + 1
                        self.map_unread, self.next_map_read = True, 0.0
                    elif self.map_unread and now >= self.next_map_read:
                        name = read_map_name(shot[0], g)
                        self.map_unread = name is None
                        self.next_map_read = now + 5          # if unreadable, try again later
                    self.map_print = picture
        return name, note


# --------------------------------------------------------------------------- tracking

class ExpTracker:
    """Turns noisy readings into trustworthy EXP gains.

    Every level needs a fixed amount of EXP (req), and the bar shows exp = pct% * req.
    req is learned from several *different* readings that agree with each other, then a
    reading only counts if its EXP and % agree with req. Misread digits break that, so
    they are ignored instead of being counted as gains.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.running = False
        self._accum = 0.0
        self._resumed = None
        self.exp = None                       # last trusted EXP
        self.pct = None
        self.last_read = None                 # latest reading, trusted or not (for display)
        self.req = None                       # EXP required for the current level
        self._seen = {}                       # distinct (exp, pct) readings this level -> weight
        self._pending = None                  # big change waiting for confirmation
        self._pending_lvl = None              # level-up waiting for confirmation
        self._level_start = None              # (exp, pct) right after a level-up, unverified
        self._prev_req = None                 # previous level's req, bounds the new one
        self.gained = 0
        self.levels = 0
        self.samples = deque()                # (elapsed, gained)

    def start(self):
        if not self.running:
            self.running = True
            self._resumed = time.monotonic()

    def pause(self):
        if self.running:
            self._accum += time.monotonic() - self._resumed
            self.running = False

    def elapsed(self):
        if self.running:
            return self._accum + time.monotonic() - self._resumed
        return self._accum

    # ---- level requirement
    @staticmethod
    def _rel_err(pct):
        # % is shown with 2 decimals, so exp / pct is only this precise
        return 0.002 + 0.01 / pct

    def _learn_req(self, candidates, confident):
        weight = 2 if confident else 1        # font-matched readings are rarely wrong
        for e, p in candidates:
            if p >= 0.2:
                self._seen[(e, p)] = max(self._seen.get((e, p), 0), weight)
        while len(self._seen) > 60:
            del self._seen[next(iter(self._seen))]
        ests = [(e / (p / 100), self._rel_err(p), w) for (e, p), w in self._seen.items()]
        if self._prev_req:                    # the next level needs about as much or more
            ests = [x for x in ests if 0.98 <= x[0] / self._prev_req <= 2.5]
        group_of = lambda c, ce: [x for x in ests if abs(x[0] - c) / c <= ce + x[1]]
        weight_of = lambda g: sum(x[2] for x in g)
        center = lambda g: sum(x[0] * x[2] / x[1] ** 2 for x in g) / sum(x[2] / x[1] ** 2 for x in g)
        groups = sorted((group_of(c, ce) for c, ce, _ in ests), key=weight_of, reverse=True)
        if not groups or weight_of(groups[0]) < 3:
            return
        best = groups[0]
        mid = center(best)
        if self.req is None:
            # Lock in only once one value clearly beats any other (misreads can agree too).
            rivals = [g for g in groups if not 0.99 <= center(g) / mid <= 1.01]
            if not rivals or weight_of(best) >= 2 * weight_of(rivals[0]):
                self.req = mid
            return
        current = group_of(self.req, 0.003)
        if 0.99 <= mid / self.req <= 1.01 or weight_of(best) < 1.5 * weight_of(current) + 1:
            if current:
                self.req = center(current)
            return
        # A different value now clearly has more support: the old one came from misreads.
        # Switch, and restart the baseline so the change isn't counted as a gain.
        self.req = mid
        self.exp = self.pct = None
        self._pending = None

    def _consistent(self, e, p):
        return abs(e / self.req * 100 - p) <= 0.02 + 0.003 * p

    def _add_gain(self, amount):
        if self.running:
            self.gained += amount
            now = self.elapsed()
            self.samples.append((now, self.gained))
            while self.samples and now - self.samples[0][0] > ROLLING_WINDOW:
                self.samples.popleft()

    # ---- main entry
    def observe(self, candidates, confident=False):
        """Feed the (exp, pct) guesses for one screenshot. Returns (accepted, note)."""
        candidates = [(e, p) for e, p in candidates if p is not None and 0 <= p < 100]
        if not candidates:
            return False, "unreadable"
        self.last_read = candidates[0]

        if self._check_level_up(candidates):
            return True, "LEVEL UP!"

        self._learn_req(candidates, confident)
        if self.req is None:
            return False, "calibrating: learns your level's EXP after your next EXP gain"

        good = [c for c in candidates if self._consistent(*c)]
        if not good:
            return False, "misread, ignored"
        e, p = good[0]
        self.last_read = good[0]

        if self._level_start:
            # First trusted reading after a level-up: fix the start value if it was misread.
            e0, p0 = self._level_start
            self._level_start = None
            if not self._consistent(e0, p0):
                fixed = round(p0 / 100 * self.req)
                self._add_gain(fixed - e0)
                self.exp = fixed

        if self.exp is None:
            self.exp, self.pct = e, p
            return True, "baseline"

        delta = e - self.exp
        if abs(delta) > 0.15 * self.req:
            # Unusually big change in one tick: only believe it if the next read agrees.
            if self._pending and abs(e - self._pending[0]) <= 0.15 * self.req:
                self._pending = None
            else:
                self._pending = (e, p)
                return False, "big jump, waiting to confirm"
        self._pending = None
        self._add_gain(delta)
        self.exp, self.pct = e, p
        return True, ""

    def _check_level_up(self, candidates):
        """% dropped a lot and EXP reset low: a level-up, once seen on two reads in a row."""
        if self.exp is None or self.pct is None or self.req is None:
            return False
        hits = []
        for e, p in candidates:
            if not (self.pct - p > 30 and e < self.exp):
                continue
            if p >= 0.2 and not (0.98 * self.req <= e / (p / 100) <= 2.5 * self.req):
                continue                      # next level's EXP must be similar or bigger
            hits.append((e, p))
        if not hits:
            self._pending_lvl = None
            return False
        if not self._pending_lvl:
            self._pending_lvl = hits[0]
            return False
        e, p = hits[0]
        self._pending_lvl = self._pending = None
        if self.running:
            self.levels += 1
        self._add_gain(self.req - self.exp + e)
        self.exp, self.pct = e, p
        self._prev_req, self.req = self.req, None
        self._seen.clear()
        self._level_start = (e, p)
        return True

    def rate(self):
        t = self.elapsed()
        return self.gained / t * 3600 if t >= 10 else None

    def rolling_rate(self):
        if len(self.samples) < 2:
            return None
        (t0, g0), (t1, g1) = self.samples[0], self.samples[-1]
        return (g1 - g0) / (t1 - t0) * 3600 if t1 - t0 >= 30 else None


class MesoTracker:
    """Turns mesos readings into mesos gained per hour and per 5 minutes.

    A new count only counts once two readings in a row agree on it (three for a big jump),
    so a one-off misread never shows up as mesos gained. A change in the
    number of digits, or by more than half, starts counting afresh from the new number
    instead: that's a shop visit or a corrected misread, not mesos picked up.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.value = None                     # confirmed mesos count
        self.seen_at = None                   # when it was last confirmed
        self._candidate = (None, 0)
        self.gained = 0
        self.samples = deque()                # (elapsed, gained)
        self.start = None                     # elapsed time when counting began
        self.counting = False

    def pause(self):
        self.counting = False                 # don't count what changes while paused

    def observe(self, value, now, elapsed, running):
        jump = False
        if value == self.value:
            self._candidate = (None, 0)           # unchanged: nothing new to confirm
        else:
            last, n = self._candidate             # how many readings in a row back the new number
            n = n + 1 if _agrees(value, last) else 1
            self._candidate = (value, n)
            old = self.value
            jump = old is not None and (len(str(value)) != len(str(old)) or abs(value - old) > 0.5 * old)
            big = old is not None and abs(value - old) > max(10000, 0.05 * old)
            if n < (3 if big or jump else 2):
                return
            self._candidate = (None, 0)
        self.seen_at = now
        if running and not self.counting:
            self.counting = True                  # count changes from this reading on
            self.start = elapsed if self.start is None else self.start
            self.samples.append((elapsed, self.gained))
        elif running and value != self.value and not jump:
            self.gained += value - self.value
            self.samples.append((elapsed, self.gained))
            while len(self.samples) > 2 and self.samples[1][0] <= elapsed - ROLLING_WINDOW:
                self.samples.popleft()
        self.value = value

    def per_hour(self, elapsed):
        if self.start is None or elapsed - self.start < 30:
            return None
        return self.gained / (elapsed - self.start) * 3600

    def per_5min(self, elapsed):
        if self.start is None:
            return None
        t0 = max(self.start, elapsed - ROLLING_WINDOW)
        if elapsed - t0 < 60:
            return None
        before = 0
        for t, g in self.samples:
            if t > t0:
                break
            before = g
        return (self.gained - before) / (elapsed - t0) * ROLLING_WINDOW


# --------------------------------------------------------------------------- auto log

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def party_label(n):
    return "Solo" if not n or n <= 1 else f"{n}ppl party"


def format_entry(e):
    """e.g. 'Oct 8th, 2026 - Rocky Road II - Lv 27 - Solo - 15,429exp/hr - 3,210mesos/hr'"""
    d = date.fromisoformat(e["date"])
    level = e["level"] if e["level"] is not None else "?"
    parts = [f"{MONTHS[d.month - 1]} {_ordinal(d.day)}, {d.year}", e["map"], f"Lv {level}"]
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    if tracked:
        parts.append(party_label(e["party"]))
    parts.append(f"{e['exp'] / e['seconds'] * 3600 if e['seconds'] else 0:,.0f}exp/hr")
    if tracked:
        ms = e.get("mesos_seconds", 0)
        parts.append(f"{e['mesos'] / ms * 3600:,.0f}mesos/hr" if ms else "?mesos/hr")
    return " - ".join(parts)


def log_row(e):
    """The log table's cells for an entry: Date, Map, Lv, Party, EXP/hr, Mesos/hr."""
    d = date.fromisoformat(e["date"])
    day = f"{MONTHS[d.month - 1]} {d.day}" + ("" if d.year == date.today().year else f" '{d.year % 100:02d}")
    level = e["level"] if e["level"] is not None else "?"
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    party = ("Solo" if not e["party"] or e["party"] <= 1 else f"{e['party']}ppl") if tracked else ""
    exp = f"{e['exp'] / e['seconds'] * 3600:,.0f}" if e["seconds"] else "—"
    ms = e.get("mesos_seconds", 0)
    mesos = f"{e['mesos'] / ms * 3600:,.0f}" if ms else ("?" if tracked else "")
    return day, e["map"], level, party, exp, mesos


def _numbers(name):
    """Roman numerals and numbers in a map name. Maps that differ in these are different maps."""
    return re.findall(r"\b(?:[IVX]+|\d+)\b", name)


LOG_VERSION = 2     # 2: mesos/hr comes from the mesos tracker (earlier values could be way off)


class AutoLog:
    """EXP/hr and mesos/hr per day, map, level and party size, measured over 5-minute
    stretches of non-stop EXP gain.

    A stretch starts with an EXP gain and is thrown away if no EXP comes in for `gap`
    seconds, or the map or party size changes. Each finished stretch is added to that day's
    entry for the map, level and party size, so the entry gets more accurate the longer you
    play there. Mesos are counted for a stretch when the mesos were read during it.

    Every change re-reads the log file first and saves straight away, so lines written by
    anyone else (another copy of the app, or by hand) are never overwritten.
    """

    def __init__(self, gap_seconds, path=LOG_PATH, text_path=LOG_TEXT_PATH):
        self.gap = gap_seconds
        self.path, self.text_path = path, text_path
        self.entries, self.aliases = [], {}           # aliases: misread/renamed map -> name to use
        if self._load() < LOG_VERSION:
            def clear_old_mesos():                    # logged by the old, unreliable mesos counting
                for e in self.entries:
                    if "party" in e:
                        e["mesos"] = e["mesos_seconds"] = 0
            self._change(clear_old_mesos)
        self.reset_clock()

    @staticmethod
    def key(e):
        return e["date"], e["map"], e["level"], e.get("party")

    def _load(self):
        """Reads the log file into memory (keeps what's there if it can't be read).
        Returns the file's format version."""
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            return LOG_VERSION
        self.entries = data.get("entries", [])
        self.aliases = data.get("aliases", {})
        return data.get("version", 1)

    def _change(self, change):
        """Applies change() to the latest log on disk and saves it."""
        self._load()
        result = change()
        self.save()
        return result

    def reset_clock(self):
        self.start = self.last_gain = self.block_map = self.block_party = self.meso0 = None
        self.block_exp = 0

    def gain(self, now, amount):
        if amount > 0:
            if self.start is None:
                self.start, self.block_exp = now, 0          # this gain starts the clock
            else:
                self.block_exp += amount
            self.last_gain = now
        elif self.start is not None:
            self.block_exp += amount                         # EXP lost, e.g. on death

    def tick(self, now, map_name, level, party=1, mesos=None):
        """Call regularly while tracking. mesos is (mesos gained so far, when last read) from
        the MesoTracker, or None. Returns the entry when one was logged."""
        if self.start is None:
            return None
        if now - self.last_gain > self.gap:
            self.reset_clock()                               # no EXP for too long: start over
            return None
        if map_name:
            if self.block_map and map_name != self.block_map:
                self.reset_clock()                           # changed maps: start over
                return None
            self.block_map = map_name
        if self.block_party is not None and party != self.block_party:
            self.reset_clock()                               # party size changed: start over
            return None
        self.block_party = party
        if self.meso0 is None and mesos and mesos[1] >= self.start - 2:
            self.meso0 = mesos                               # mesos as the stretch began
        if now - self.start < LOG_BLOCK:
            return None
        meso_gain = meso_seconds = 0
        if self.meso0 and mesos and mesos[1] > self.meso0[1]:
            meso_gain, meso_seconds = mesos[0] - self.meso0[0], mesos[1] - self.meso0[1]
        entry = self._add(self.block_map or "Unknown map", level, party, self.block_exp,
                          now - self.start, meso_gain, meso_seconds)
        self.start, self.block_exp = now, 0                  # the next 5 minutes start now
        self.meso0 = mesos if mesos and now - mesos[1] < 10 else None
        return entry

    def seconds_left(self, now):
        return None if self.start is None else max(0.0, LOG_BLOCK - (now - self.start))

    def _add(self, map_name, level, party, exp, seconds, mesos, meso_seconds):
        def add():
            key = (date.today().isoformat(), map_name, level, party)
            for e in self.entries:
                if self.key(e) == key:
                    e["exp"] += exp
                    e["seconds"] += seconds
                    e["mesos"] = e.get("mesos", 0) + mesos
                    e["mesos_seconds"] = e.get("mesos_seconds", 0) + meso_seconds
                    break
            else:
                e = {"date": key[0], "map": map_name, "level": level, "party": party, "exp": exp,
                     "seconds": seconds, "mesos": mesos, "mesos_seconds": meso_seconds}
                self.entries.append(e)
            e["updated"] = time.time()
            return e
        return self._change(add)

    def canonical(self, name):
        """The name to log a map under: applies renames and fixes small misreads."""
        name = self.aliases.get(name, name)
        best, score = name, 0.0
        for known in {e["map"] for e in self.entries} | set(self.aliases.values()):
            if _numbers(known) == _numbers(name):
                s = SequenceMatcher(None, known.lower(), name.lower()).ratio()
                if s > score:
                    best, score = known, s
        return best if score >= 0.85 else name

    def rename(self, entry, new_name):
        old, key = entry["map"], self.key(entry)

        def apply():
            if old == "Unknown map":
                for e in self.entries:                       # just this one
                    if self.key(e) == key:
                        e["map"] = new_name
            else:
                self.aliases[old] = new_name                 # and future reads of the old name
                for k, v in self.aliases.items():
                    if v == old:
                        self.aliases[k] = new_name
                for e in self.entries:
                    if e["map"] == old:
                        e["map"] = new_name
            merged = {}
            for e in self.entries:                           # combine entries that now match
                if self.key(e) in merged:
                    m = merged[self.key(e)]
                    for field in ("exp", "seconds", "mesos", "mesos_seconds"):
                        if field in e:
                            m[field] = m.get(field, 0) + e[field]
                    m["updated"] = max(m.get("updated", 0), e.get("updated", 0))
                else:
                    merged[self.key(e)] = e
            self.entries = list(merged.values())

        self._change(apply)
        if old != "Unknown map" and self.block_map == old:
            self.block_map = new_name

    def delete(self, entry):
        key = self.key(entry)

        def apply():
            self.entries = [e for e in self.entries if self.key(e) != key]

        self._change(apply)

    def newest_first(self):
        return sorted(self.entries, key=lambda e: e.get("updated", 0), reverse=True)

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": LOG_VERSION, "entries": self.entries,
                                       "aliases": self.aliases}, indent=1))
            os.replace(tmp, self.path)
            self.text_path.write_text("".join(format_entry(e) + "\n" for e in self.newest_first()))
        except Exception as e:
            log_error("saving the EXP log", e)


# --------------------------------------------------------------------------- UI helpers

def fmt_num(n):
    return "—" if n is None else f"{n:,.0f}"


def fmt_short(n):
    if n is None:
        return "—"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.2f}{suffix}"
    return f"{n:,.0f}"


def fmt_dur(seconds):
    if seconds is None or seconds < 0 or seconds > 99 * 3600:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class RegionSelector:
    """Full-screen translucent overlay; drag a box around the EXP bar text."""

    def __init__(self, root, on_done):
        self.on_done = on_done
        with mss.mss() as sct:
            v = sct.monitors[0]
        self.origin = (v["left"], v["top"])
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        self.win.attributes("-alpha", 0.35)
        self.win.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.win, bg="black", cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(v["width"] // 2, 60, fill="white", font=("Segoe UI", 20, "bold"),
                                text="Drag a box around the EXP numbers  (Esc to cancel)")
        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.focus_force()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#ffcc00", width=2)

    def _drag(self, e):
        if self.rect:
            self.canvas.coords(self.rect, *self.start, e.x, e.y)

    def _release(self, e):
        if not self.start:
            return
        x0, x1 = sorted((self.start[0], e.x))
        y0, y1 = sorted((self.start[1], e.y))
        if x1 - x0 < 10 or y1 - y0 < 5:
            self._finish(None)
            return
        self._finish({"left": self.origin[0] + x0, "top": self.origin[1] + y0,
                      "width": x1 - x0, "height": y1 - y0})

    def _finish(self, region):
        self.win.destroy()
        self.on_done(region)


# --------------------------------------------------------------------------- app

BG, FG, DIM, ACCENT, FIELD = "#1b1d23", "#e8e8e8", "#8a8f98", "#ffcc33", "#2c3038"
LIST_BG, LIST_BG2 = "#23262d", "#282b33"
LOG_HINT = "Click a line for details. Right-click to copy, rename or delete it."


class App:
    def __init__(self, root, templates):
        self.root = root
        self.cfg = {**DEFAULTS, **load_config()}
        mode = self.cfg.get("mode", "window")
        self.reader = ScreenReader(templates, mode, self.cfg.get("region"))
        saved = self.cfg.get("mesos_box")
        if isinstance(saved, list) and len(saved) == 6:
            self.reader.saved_mesos = tuple(saved)
        self.tracker = ExpTracker()
        self.meso = MesoTracker()
        self.party = 1
        self._party_seen = (None, 0)
        self.last_mesos = None
        self.rejected = 0
        self.autolog = AutoLog(self.cfg["gap_seconds"])
        self.map_name = None
        self.level = None
        self.note = None
        self.last_logged = None
        self.log_open = False
        self.settings_win = None
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.last_capture = None

        root.title(WINDOW_TITLE)
        try:
            root.iconbitmap(default=str(ICON_PATH))
        except Exception:
            pass
        root.report_callback_exception = lambda kind, exc, tb: self._oops("a button or menu", exc)
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._apply_window_settings()
        self._restore_position()

        main = tk.Frame(root, bg=BG)
        main.grid(row=0, column=0, sticky="n")
        self.vars = {}
        tk.Label(main, text="EXP / hr", bg=BG, fg=DIM, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        corner = tk.Frame(main, bg=BG)
        corner.grid(row=0, column=1, sticky="ne", padx=(0, 6), pady=(4, 0))
        small = dict(bg=BG, fg=DIM, activebackground=FIELD, activeforeground=FG, relief="flat",
                     bd=0, padx=6, pady=0, font=("Segoe UI", 11), cursor="hand2")
        tk.Button(corner, text="⚙", command=self.open_settings, **small).pack(side="left")
        self.log_btn = tk.Button(corner, text=">", command=self.toggle_log, **small)
        self.log_btn.pack(side="left")
        self.big = tk.Label(main, text="—", bg=BG, fg=ACCENT, font=("Segoe UI", 24, "bold"))
        self.big.grid(row=1, column=0, columnspan=2, sticky="w", padx=12)

        rows = ["Last 5 min", "% / hr", "Gained", "Current", "To level",
                "Mesos", "Mesos / hr", "Mesos / 5 min", "Elapsed"]
        for i, name in enumerate(rows, start=2):
            tk.Label(main, text=name, bg=BG, fg=DIM, font=("Segoe UI", 10)).grid(
                row=i, column=0, sticky="w", padx=(12, 8))
            v = tk.StringVar(value="—")
            tk.Label(main, textvariable=v, bg=BG, fg=FG, font=("Consolas", 11)).grid(
                row=i, column=1, sticky="e", padx=(8, 12))
            self.vars[name] = v

        bar = tk.Frame(main, bg=BG)
        bar.grid(row=len(rows) + 2, column=0, columnspan=2, pady=(10, 4), padx=8)
        btn = dict(bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG,
                   relief="flat", padx=8, pady=3, font=("Segoe UI", 9))
        self.start_btn = tk.Button(bar, text="Start", command=self.toggle, **btn)
        self.start_btn.pack(side="left", padx=2)
        tk.Button(bar, text="Reset", command=self.reset, **btn).pack(side="left", padx=2)
        tk.Button(bar, text="Follow game", command=self.follow_game, **btn).pack(side="left", padx=2)
        tk.Button(bar, text="Select area", command=self.select_area, **btn).pack(side="left", padx=2)
        tk.Button(bar, text="View capture", command=self.view_capture, **btn).pack(side="left", padx=2)

        self.status = tk.StringVar()
        tk.Label(main, textvariable=self.status, bg=BG, fg=DIM, font=("Segoe UI", 8),
                 wraplength=380, justify="left").grid(
            row=len(rows) + 3, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

        self._build_log_panel()
        if self.cfg.get("log_open"):
            self.toggle_log()

        if not templates:
            self.status.set("digits.npz is missing or outdated, so only Tesseract can read "
                            "the bar (less reliable).")
        else:
            self.status.set("Looking for the MapleStory window..." if mode == "window"
                            else "Using your selected area.")

        threading.Thread(target=self._worker, daemon=True).start()
        root.after(200, self._pump)
        root.after(500, self._refresh)

    # ---- log panel
    def _build_log_panel(self):
        p = self.log_panel = tk.Frame(self.root, bg=BG)
        tk.Label(p, text="EXP log", bg=BG, fg=DIM, font=("Segoe UI", 9)).grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.log_status = tk.StringVar()
        tk.Label(p, textvariable=self.log_status, bg=BG, fg=FG, font=("Segoe UI", 9),
                 justify="left", anchor="w").grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 6))

        # A compact table: one row per day, map, level and party size.
        cell = tkfont.Font(family="Segoe UI", size=9)
        pt = float(self.root.tk.call("tk", "scaling"))         # screen pixels per point
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.layout("Log.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Log.Treeview", background=LIST_BG, fieldbackground=LIST_BG, foreground=FG,
                        font=cell, rowheight=int(cell.metrics("linespace") * 1.45), borderwidth=0)
        style.configure("Log.Treeview.Heading", background=FIELD, foreground=DIM, relief="flat",
                        borderwidth=0, font=("Segoe UI", 8))
        style.map("Log.Treeview", background=[("selected", "#3a3f49")], foreground=[("selected", FG)])
        style.map("Log.Treeview.Heading", background=[("active", "#3a3f49")])
        columns = (("date", "Date", 40, "w"), ("map", "Map", 124, "w"), ("lv", "Lv", 20, "center"),
                   ("party", "Party", 34, "center"), ("exp", "EXP/hr", 48, "e"), ("mesos", "Mesos/hr", 50, "e"))
        tree = self.log_tree = ttk.Treeview(p, columns=[c[0] for c in columns], show="headings",
                                            style="Log.Treeview", height=11, selectmode="browse")
        for key, title, width, anchor in columns:
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=int(width * pt), minwidth=int(width * pt), anchor=anchor, stretch=False)
        tree.tag_configure("odd", background=LIST_BG2)
        tree.grid(row=2, column=0, sticky="nsew")
        scroll = tk.Scrollbar(p, orient="vertical", command=tree.yview)
        scroll.grid(row=2, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        self.log_hint = tk.StringVar(value=LOG_HINT)
        tk.Label(p, textvariable=self.log_hint, bg=BG, fg=DIM, font=("Segoe UI", 8), justify="left",
                 anchor="w", wraplength=int(sum(c[2] for c in columns) * pt)).grid(
            row=3, column=0, columnspan=2, sticky="we", pady=(4, 0))
        tree.bind("<Button-3>", self._log_menu)
        tree.bind("<<TreeviewSelect>>", self._log_selected)
        self.shown = {}
        self._fill_log()

    def toggle_log(self):
        self.log_open = not self.log_open
        if self.log_open:
            self.log_panel.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=(10, 8))
        else:
            self.log_panel.grid_remove()
        self.log_btn.config(text="<" if self.log_open else ">")
        self.cfg["log_open"] = self.log_open
        save_config(self.cfg)

    def _fill_log(self):
        tree = self.log_tree
        tree.delete(*tree.get_children())
        self.shown = {}
        for i, e in enumerate(self.autolog.newest_first()):
            iid = tree.insert("", "end", values=log_row(e), tags=("odd",) if i % 2 else ())
            self.shown[iid] = e
        self.log_hint.set(LOG_HINT if self.shown else "No logs yet. Press Start and gain EXP for 5 minutes.")

    def _log_selected(self, _event):
        sel = self.log_tree.selection()
        e = self.shown.get(sel[0]) if sel else None
        if e:
            self.log_hint.set(f"{e['map']}: measured over {e['seconds'] / 60:.0f} min of non-stop EXP "
                              "gain. Right-click to copy, rename or delete.")

    def _log_menu(self, event):
        iid = self.log_tree.identify_row(event.y)
        entry = self.shown.get(iid)
        if not entry:
            return
        self.log_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Copy line", command=lambda: self._copy(entry))
        menu.add_command(label="Rename map...", command=lambda: self._rename(entry))
        menu.add_command(label="Delete entry", command=lambda: self._delete(entry))
        menu.tk_popup(event.x_root, event.y_root)

    def _copy(self, entry):
        self.root.clipboard_clear()
        self.root.clipboard_append(format_entry(entry))
        self.log_hint.set("Copied: " + format_entry(entry))

    def _rename(self, entry):
        name = simpledialog.askstring("Rename map", "Map name:", initialvalue=entry["map"],
                                      parent=self.root)
        if name and name.strip():
            self.autolog.rename(entry, name.strip())
            if self.map_name:
                self.map_name = self.autolog.canonical(self.map_name)
            self._fill_log()

    def _delete(self, entry):
        if messagebox.askyesno("Delete entry", f"Delete this log entry?\n\n{format_entry(entry)}",
                               parent=self.root):
            self.autolog.delete(entry)
            self._fill_log()

    def _log_status_text(self, now):
        where = (f"{self.map_name or 'Map not read yet'} · Lv {self.level or '?'} · "
                 f"{party_label(self.party)}")
        if not self.tracker.running:
            return f"{where}\nPress Start to log EXP/hr here."
        left = self.autolog.seconds_left(now)
        if left is None:
            text = f"{where}\nWaiting for EXP gain..."
        else:
            text = f"{where}\nNext log in {int(left) // 60}:{int(left) % 60:02d} of non-stop EXP gain"
        if self.last_logged and now - self.last_logged < 8:
            text += "  (just logged)"
        return text

    # ---- settings
    def _apply_window_settings(self):
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.root.attributes("-alpha", 1 - min(90, max(0, int(self.cfg["transparency"]))) / 100)

    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        w = self.settings_win = tk.Toplevel(self.root)
        w.title("Settings")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.transient(self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        text = dict(bg=BG, fg=FG, font=("Segoe UI", 9))

        gap = tk.StringVar(value=str(self.cfg["gap_seconds"]))
        tk.Label(w, text="Restart the 5-minute log clock after no EXP for", **text).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 4))
        line = tk.Frame(w, bg=BG)
        line.grid(row=1, column=0, sticky="w", padx=14)
        tk.Spinbox(line, from_=5, to=300, width=5, textvariable=gap, bg=FIELD, fg=FG,
                   buttonbackground=FIELD, insertbackground=FG, relief="flat",
                   font=("Segoe UI", 9)).pack(side="left")
        tk.Label(line, text="seconds", **text).pack(side="left", padx=(6, 0))

        transparency = tk.IntVar(value=int(self.cfg["transparency"]))
        tk.Label(w, text="Window transparency (%)", **text).grid(
            row=2, column=0, sticky="w", padx=14, pady=(14, 0))
        tk.Scale(w, from_=0, to=90, orient="horizontal", variable=transparency, length=240,
                 bg=BG, fg=FG, troughcolor=FIELD, highlightthickness=0, bd=0,
                 font=("Segoe UI", 8)).grid(row=3, column=0, sticky="w", padx=10)

        topmost = tk.BooleanVar(value=bool(self.cfg["topmost"]))
        tk.Checkbutton(w, text="Always on top of other windows", variable=topmost, bg=BG, fg=FG,
                       selectcolor=FIELD, activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 9)).grid(row=4, column=0, sticky="w", padx=10, pady=(10, 0))

        def apply(*_):
            try:
                seconds = int(gap.get())
            except ValueError:
                seconds = 0
            if 1 <= seconds <= 3600:
                self.cfg["gap_seconds"] = self.autolog.gap = seconds
            self.cfg["transparency"] = int(transparency.get())
            self.cfg["topmost"] = bool(topmost.get())
            self._apply_window_settings()
            w.attributes("-topmost", self.cfg["topmost"])
            save_config(self.cfg)

        for var in (gap, transparency, topmost):
            var.trace_add("write", apply)
        buttons = tk.Frame(w, bg=BG)
        buttons.grid(row=5, column=0, sticky="we", padx=14, pady=(14, 6))
        style = dict(bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG, relief="flat",
                     padx=12, pady=3, font=("Segoe UI", 9))
        tk.Button(buttons, text="Open data folder", command=self.open_data_folder, **style).pack(side="left")
        tk.Button(buttons, text="Close", command=w.destroy, **style).pack(side="right")
        pt = float(self.root.tk.call("tk", "scaling"))         # screen pixels per point
        tk.Label(w, text=f"Your log and settings are saved in {DATA_DIR}", bg=BG, fg=DIM,
                 font=("Segoe UI", 8), wraplength=int(250 * pt), justify="left").grid(
            row=6, column=0, sticky="w", padx=14)
        tk.Label(w, text=f"Maple EXP/hr v{VERSION}. This is a beta: it's still being improved.",
                 bg=BG, fg=DIM, font=("Segoe UI", 8)).grid(row=7, column=0, sticky="w", padx=14, pady=(2, 12))

    def open_data_folder(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(DATA_DIR)
        except Exception as e:
            self._oops("opening the data folder", e)

    def _restore_position(self):
        pos = self.cfg.get("pos")
        if not pos:
            return
        with mss.mss() as sct:
            v = sct.monitors[0]
        if (v["left"] <= pos[0] < v["left"] + v["width"] - 50
                and v["top"] <= pos[1] < v["top"] + v["height"] - 50):
            self.root.geometry(f"+{pos[0]}+{pos[1]}")    # only if it's still on a screen

    def _window_rect(self):
        """This app's window on screen (physical pixels), or None."""
        try:
            hwnd = _GetAncestor(self.root.winfo_id(), 2)       # GA_ROOT: the framed window
            r = wintypes.RECT()
            if _GetWindowRect(hwnd, ctypes.byref(r)):
                return r.left, r.top, r.right - r.left, r.bottom - r.top
        except Exception:
            pass
        return None

    # ---- background capture loop
    def _worker(self):
        with mss.mss() as sct:
            while not self.stop_evt.is_set():
                t0 = time.monotonic()
                try:
                    self.q.put(("reading", self.reader.read(sct)))
                except Exception as e:
                    log_error("reading the screen", e)
                    self.q.put(("error", f"{type(e).__name__}: {e}"))
                self.stop_evt.wait(max(0.1, POLL_SECONDS - (time.monotonic() - t0)))

    def _oops(self, what, exc):
        """Something unexpected went wrong: note it in errors.log and keep going."""
        log_error(what, exc)
        self.status.set("Something went wrong, but the tracker keeps running. Details are in "
                        "errors.log (Settings > Open data folder).")

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "error":
                    self.status.set(f"Error: {payload}")
                else:
                    self._handle(payload)
        except queue.Empty:
            pass
        except Exception as e:
            self._oops("handling a reading", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(200, self._pump)

    def _handle(self, r):
        t, now = self.tracker, time.monotonic()
        if r.image is not None:
            self.last_capture = r
        if r.mesos_image is not None:
            self.last_mesos = r
        if r.mesos_at:                                  # remember where the mesos box is
            self.cfg["mesos_box"] = list(r.mesos_at)
            self.reader.saved_mesos = tuple(r.mesos_at)
            save_config(self.cfg)
        if r.mesos is not None:
            self.meso.observe(r.mesos, now, t.elapsed(), t.running)
        if r.party is not None:                         # believe a new party size once seen twice
            seen, n = self._party_seen
            n = n + 1 if r.party == seen else 1
            self._party_seen = (r.party, n)
            if n >= 2:
                self.party = r.party
        if r.map:
            self.map_name = self.autolog.canonical(r.map)
        self.note = r.note
        if r.candidates:
            before = t.gained
            ok, note = t.observe(r.candidates, r.confident)
            self.rejected = 0 if ok else self.rejected + 1
            if self.rejected >= 10:                 # keeps not adding up: find the EXP bar again
                self.reader.box, self.rejected = None, 0
            if t.running and t.gained != before:
                self.autolog.gain(now, t.gained - before)
            if note == "LEVEL UP!" and self.level:
                self.level += 1                 # until the new level's EXP confirms it
            status = f"{r.where}. Read {r.text}  {note}".strip()
        else:
            status = r.where
        if self.note:
            status += f"\n{self.note}"
        self.status.set(status)
        self.level = level_for_req(t.req) or self.level
        self._log_tick(now)

    def _log_tick(self, now):
        if not self.tracker.running:
            return
        m = self.meso
        mesos = (m.gained, m.seen_at) if m.value is not None and now - m.seen_at <= 30 else None
        entry = self.autolog.tick(now, self.map_name, self.level, self.party, mesos)
        if entry:
            self.last_logged = now
            self._fill_log()

    def _refresh(self):
        try:
            self._update_display()
        except Exception as e:
            self._oops("updating the window", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(500, self._refresh)

    def _update_display(self):
        t, now = self.tracker, time.monotonic()
        rate = t.rate()
        self.big.config(text=fmt_num(rate) if rate is not None else "—")
        self.vars["Last 5 min"].set(fmt_num(t.rolling_rate()))
        self.vars["% / hr"].set(f"{rate / t.req * 100:.2f}%" if rate and t.req else "—")
        lvl = f"  (+{t.levels} lvl)" if t.levels else ""
        self.vars["Gained"].set(f"{fmt_short(t.gained)}{lvl}")
        shown = (t.exp, t.pct) if t.exp is not None else t.last_read
        if shown:
            self.vars["Current"].set(f"{fmt_num(shown[0])} [{shown[1]:.2f}%]")
        best_rate = t.rolling_rate() or rate
        if t.req and t.exp is not None and best_rate and best_rate > 0:
            self.vars["To level"].set(fmt_dur((t.req - t.exp) / best_rate * 3600))
        else:
            self.vars["To level"].set("—")
        m, el = self.meso, t.elapsed()
        if m.value is None:
            self.vars["Mesos"].set("open inventory")
        else:
            self.vars["Mesos"].set(fmt_num(m.value) + (f"  (+{fmt_short(m.gained)})" if m.gained > 0 else ""))
        self.vars["Mesos / hr"].set(fmt_num(m.per_hour(el)))
        self.vars["Mesos / 5 min"].set(fmt_num(m.per_5min(el)))
        self.vars["Elapsed"].set(fmt_dur(el))
        self._log_tick(now)
        self.log_status.set(self._log_status_text(now))
        self.reader.own_rect = self._window_rect()

    # ---- buttons
    def toggle(self):
        if self.tracker.running:
            self.tracker.pause()
            self.meso.pause()
            self.start_btn.config(text="Resume")
        else:
            self.tracker.start()
            self.reader.request_map()           # read the map name now
            self.start_btn.config(text="Pause")
        self.autolog.reset_clock()

    def reset(self):
        self.tracker.reset()
        self.meso.reset()
        self.autolog.reset_clock()
        self.start_btn.config(text="Start")
        self.status.set("Reset.")

    def _new_source(self):
        # New place to read from -> new baseline, but keep the session totals.
        self.tracker.exp = self.tracker.pct = None
        self.tracker._pending = self.tracker._pending_lvl = None
        self.autolog.reset_clock()
        save_config(self.cfg)

    def follow_game(self):
        self.reader.use_window()
        self.cfg["mode"] = "window"
        self._new_source()
        self.status.set("Following the MapleStory window...")

    def select_area(self):
        def done(region):
            if region:
                self.reader.use_region(region)
                self.cfg.update(mode="region", region=region)
                self._new_source()
                self.status.set("Using your selected area. Click Follow game to go back to "
                                "finding it automatically.")
        RegionSelector(self.root, done)

    @staticmethod
    def _boxed(bgr, boxes):
        """A screenshot crop, enlarged, with a box drawn around each digit found."""
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        scale = min(4.0, max(1.0, 120 / img.height), 900 / img.width)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        draw = ImageDraw.Draw(img)
        colors = {"exp": "#ffcc33", "pct": "#33ccff", "mesos": "#66dd66"}
        for x0, y0, x1, y1, kind in boxes:
            draw.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                           outline=colors[kind], width=2)
        return ImageTk.PhotoImage(img)

    def view_capture(self):
        r, mr = self.last_capture, self.last_mesos
        if not r and not mr:
            self.status.set("Nothing captured yet.")
            return
        win = tk.Toplevel(self.root)
        win.title("Last capture")
        win.configure(bg=BG)
        win.attributes("-topmost", bool(self.cfg["topmost"]))
        shots = []
        if r:
            shots.append((r.image, r.boxes, f"EXP bar. Read: {r.text or 'nothing'}"))
        if mr:
            read = fmt_num(mr.mesos) if mr.mesos is not None else "nothing (waiting for a clear look)"
            shots.append((mr.mesos_image, mr.mesos_boxes, f"Mesos. Read: {read}"))
        win._imgs = []
        for shot, boxes, text in shots:
            win._imgs.append(self._boxed(shot, boxes))
            tk.Label(win, image=win._imgs[-1], bg=BG).pack(padx=8, pady=(8, 2))
            tk.Label(win, text=text, bg=BG, fg=FG).pack(anchor="w", padx=8)
        tk.Label(win, text="Boxes show where each digit was found.", bg=BG, fg=DIM).pack(
            anchor="w", padx=8, pady=(4, 8))

    def close(self):
        self.stop_evt.set()
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(self.cfg)
        self.root.destroy()


def main():
    global tesseract_ok, _single_instance
    # Only one copy at a time (per data folder): two copies would each keep their own log.
    # If it's already running, bring that window to the front instead.
    lock = "Local\\MapleEXP-" + hashlib.md5(str(DATA_DIR).lower().encode()).hexdigest()[:12]
    _single_instance = _CreateMutex(None, False, lock)
    if ctypes.get_last_error() == 183:                      # ERROR_ALREADY_EXISTS
        hwnd = _FindWindow(None, WINDOW_TITLE)
        if hwnd:
            _ShowWindow(hwnd, 9)                            # SW_RESTORE
            _SetForegroundWindow(hwnd)
        return
    move_old_data()
    tess = find_tesseract()
    if tess:
        pytesseract.pytesseract.tesseract_cmd = tess
        tesseract_ok = True
    root = tk.Tk()
    App(root, DigitTemplates.load())
    root.mainloop()


if __name__ == "__main__":
    main()

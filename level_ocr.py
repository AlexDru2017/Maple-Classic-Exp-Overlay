"""Reading the character's level from the status bar's "LV." badge: white digits in an orange box, far
left of the EXP bar. It is only used when the bar shows no EXP number, so the level can't come from
number and % (see ExpTracker.observe_percent).

The level is never guessed: the label "LV" has to be there, a complete box has to be found, and the
digits are read at several sizes in one Tesseract run, which must agree. A box that is covered or cut
off gives nothing. The character's name is never read as text (name_print below keeps only a small picture
of it, in memory, to tell characters apart).
"""
import re
from collections import Counter

import cv2
import numpy as np

VOTES = 4                    # of the 6 digit pictures, this many must read the same level
SIZES = (64, 80)             # heights, in px, the digits are blown up to (grey picture)
MASK_SIZES = (48, 64, 80, 96)    # ...and the black-and-white picture


def find_badge(bgr, x0, cy, gh):
    """(crop of the badge row, (x, y, w, h) of the orange box in it), or None.
    x0, cy, gh: where the EXP number starts, the bar's middle line, the digit height."""
    if bgr is None or not bgr.size or gh < 5:
        return None
    # (the badge sits a little lower than the EXP text, in the middle of the status panel)
    left, top = max(0, int(x0 - 72 * gh)), max(0, int(cy - 1.6 * gh))
    right, bottom = max(0, int(x0 - 38 * gh)), min(bgr.shape[0], int(cy + 3.6 * gh))
    roi = bgr[top:bottom, left:right]
    if roi.shape[0] < 3 * gh or roi.shape[1] < 6 * gh:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    orange = ((hsv[..., 0] >= 4) & (hsv[..., 0] <= 35) & (hsv[..., 1] >= 100) & (hsv[..., 2] >= 130)).astype(np.uint8)
    # (the digits' white strokes cut the box up: close the gaps, or it falls apart into pieces)
    orange = cv2.morphologyEx(orange, cv2.MORPH_CLOSE, np.ones((max(2, round(0.5 * gh)),) * 2, np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(orange, connectivity=8)
    boxes = [(int(x), int(y), int(w), int(h)) for x, y, w, h, area in stats[1:]
             if 1.2 * gh <= h <= 2.7 * gh and 0.6 * gh <= w <= 6 * gh and area >= 0.3 * w * h]
    # A box touching the edge of what was looked at is cut off (covered by the overlay or a window).
    boxes = [b for b in boxes if b[1] > 0 and b[1] + b[3] < roi.shape[0] and b[0] > 0 and b[0] + b[2] < roi.shape[1]]
    if len(boxes) != 1:
        return None
    x, y, w, h = boxes[0]
    pad = max(1, round(0.35 * gh))
    row = roi[max(0, y - pad):y + h + pad, max(0, x - int(5 * gh)):x + w + pad]
    return row.copy(), (min(x, int(5 * gh)), min(y, pad), w, h)


def has_label(row, box, gh):
    """Is there an "LV." left of the box: white letters about as tall as the digits, a letter or two wide?
    (Checked by shape, not by reading it: at small window sizes the letters are too few pixels to read,
    and touch each other.)"""
    bx = box[0]
    hsv = cv2.cvtColor(row[:, :max(1, bx - round(0.25 * gh))], cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < 100) & (hsv[..., 2] >= 170)).astype(np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    letters = [(x, w) for x, _, w, h, _ in stats[1:] if 0.9 * gh <= h <= 2.2 * gh and w <= 2.5 * gh]
    return bool(letters) and max(x + w for x, w in letters) - min(x for x, _ in letters) >= gh


def _pictures(row, box, gh):
    """The digit pictures, black text on white, for Tesseract (None if the box has no inside)."""
    _, by, w, h = box
    bx = box[0]
    margin = max(1, round(h * 0.1))
    # Blue separates the white strokes from the orange: in a grey picture this font's 3/8 and 2/6 blur together.
    digits = row[by + margin:by + h - margin, bx + margin:bx + w - margin, 0]
    if digits.size == 0:
        return None

    def ready(image, target):
        big = cv2.resize(image, None, fx=target / h, fy=target / h, interpolation=cv2.INTER_CUBIC)
        return cv2.copyMakeBorder(255 - np.clip(big, 0, 255).astype(np.uint8), 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)

    return [ready(digits, s) for s in SIZES] + [ready((digits > 100).astype(np.uint8) * 255, s) for s in MASK_SIZES]


def read_level(bgr, x0, cy, gh, tess, look=None):
    """The level (1-250) read from the badge, or None. look (a dict) gets the badge picture, the votes and why."""
    if look is not None:
        look.clear()
        look.update(image=None, why="", votes={})

    def fail(why):
        if look is not None:
            look["why"] = why
        return None

    found = find_badge(bgr, x0, cy, gh)
    if found is None:
        return fail("no complete level badge in sight")
    row, box = found
    if look is not None:
        look["image"] = row
    if tess is None:
        return fail("Tesseract isn't available")
    if not has_label(row, box, gh):
        return fail("the LV label wasn't there")
    pictures = _pictures(row, box, gh)
    if pictures is None:
        return fail("the level badge is cut off")
    votes = Counter()
    for text in tess.read_lines(pictures, "0123456789"):
        match = re.fullmatch(r"\s*([1-9][0-9]{0,2})\s*", text)
        if match and 1 <= int(match[1]) <= 250:
            votes[int(match[1])] += 1
    if look is not None:
        look["votes"] = dict(votes)
    best = votes.most_common()
    if not best or best[0][1] < VOTES or (len(best) > 1 and best[1][1] >= 2):
        return fail("the sizes didn't agree on a level")
    return best[0][0]


# ---- telling characters apart (never by reading the name: only a small picture of it, kept in memory)
PRINT_SIZE = (96, 12)        # the name row's white pixels, shrunk to this many columns and rows
MIN_INK = 8.0                # a name row with less white than this (in shrunk pixels) isn't there
SAME_NAME = 0.75             # how alike every part of two prints must be (correlation) to be the same name
CHUNK = 12                   # columns of a print compared at a time: one different letter must show


def name_print(bgr, x0, cy, gh):
    """A small picture of the character's name (the white text under the class), or None when there
    is no text to see (covered, off screen). x0, cy, gh as for find_badge. It is only ever compared
    with another print from this session: it is not saved, shown or sent, and the name isn't read."""
    if bgr is None or not bgr.size or gh < 5:
        return None
    left, right = int(x0 - 50.5 * gh), int(x0 - 38 * gh)
    top, bottom = int(cy + 0.9 * gh), int(cy + 2.6 * gh)
    if left < 0 or top < 0 or right > bgr.shape[1] or bottom > bgr.shape[0] or right - left < 4 * gh:
        return None
    hsv = cv2.cvtColor(bgr[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < 90) & (hsv[..., 2] >= 170)).astype(np.float32)
    small = cv2.resize(white, PRINT_SIZE, interpolation=cv2.INTER_AREA) * 4
    return small if small.sum() >= MIN_INK else None


def same_name(a, b):
    """Are two prints of the same name? (False for any print that isn't there.) Strict on purpose: a wrong
    "same" would carry one character's level over to another, a wrong "different" only asks again. Every
    part of the row has to match, so "Bean2" and "Bean3" differ even though most of the row is alike."""
    if a is None or b is None:
        return False
    best = 0.0
    for shift in (-2, -1, 0, 1, 2):          # (the text may sit a pixel or so to either side)
        x, y = (a[:, shift:], b[:, :b.shape[1] - shift]) if shift >= 0 else (a[:, :shift], b[:, -shift:])
        worst = 1.0
        for c in range(0, x.shape[1] - CHUNK + 1, CHUNK // 2):
            xc, yc = x[:, c:c + CHUNK], y[:, c:c + CHUNK]
            nx, ny = np.linalg.norm(xc), np.linalg.norm(yc)
            if nx >= 1.0 or ny >= 1.0:         # (nothing to compare where neither has any text)
                worst = min(worst, float((xc * yc).sum()) / (max(nx, 1e-6) * max(ny, 1e-6)))
        best = max(best, worst)
    return best >= SAME_NAME

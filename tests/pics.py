"""Pictures the tests make from the real EXP bar frames (tests/frames/exp_*.png): no new screenshots needed.
Import it after maple_exp (the tests do that first, on their own temp data folder)."""
import cv2
import numpy as np
import maple_exp as m


def percent_only(bgr):
    """The same bar the way the game draws it past a million EXP: the number cut out, "EXP. [71.68%]"."""
    lay = m.find_exp(bgr)
    start, last = int(lay.box[0]), lay.exp[-1]
    return np.hstack([bgr[:, :start], bgr[:, int(last[0] + last[1] / 2) + 1:]])


def badge_scene(level, gh, label=True, box=True, name=None, shift=0, noise=0):
    """A status bar with an "LV." badge drawn the way the game lays it out, far left of the EXP text
    (digits gh px tall). Returns (picture, x0, cy): where the EXP number would start and its middle line.
    label/box: leave out the "LV." or the orange box. name: the character's name, drawn under the class
    (shift px to the right, noise: random grain, 0 for none)."""
    h, w = int(9 * gh), int(80 * gh)
    img = np.full((h, w, 3), (62, 56, 50), np.uint8)
    x0, cy = int(60 * gh), int(4 * gh)
    bw, bh = int((0.9 + 1.05 * len(str(level))) * gh), int(1.75 * gh)
    bx, by = int(x0 - 53.6 * gh), int(cy + 0.2 * gh)
    if box:
        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), (59, 129, 203), -1)
        scale = bh * 0.62 / 22
        (tw, th), _ = cv2.getTextSize(str(level), cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, round(gh / 5)))
        cv2.putText(img, str(level), (bx + (bw - tw) // 2, by + (bh + th) // 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (250, 240, 235), max(1, round(gh / 5)))
    if label:
        scale = 1.5 * gh / 22
        cv2.putText(img, "LV.", (int(bx - 5.2 * gh), by + bh), cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (240, 240, 240), max(1, round(gh / 5)))
    if name:
        scale = 1.2 * gh / 22
        cv2.putText(img, name, (int(x0 - 49 * gh) + shift, int(cy + 2.35 * gh)), cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (235, 235, 235), max(1, round(gh / 8)))
    if noise:
        grain = np.random.default_rng(1).normal(0, noise, img.shape)
        img = np.clip(img + grain, 0, 255).astype(np.uint8)
    return img, x0, cy


def relabelled(bgr, text):
    """The percentage-only bar with its "EXP." label wiped, and `text` (HP, MP...) drawn there instead."""
    pic = percent_only(bgr)
    lay = m.find_exp(pic)
    x1, y0, y1 = int(lay.box[0]), int(lay.cy - 1.6 * lay.gh), int(lay.cy + 1.6 * lay.gh)
    pic[y0:y1, :x1] = np.median(pic[y0:y1, :2].reshape(-1, 3), axis=0)
    if text:
        h = int(lay.gh * 1.3)
        cv2.putText(pic, text, (2, int(lay.cy + h / 2)), cv2.FONT_HERSHEY_SIMPLEX, h / 22, (255, 255, 255), 1)
    return pic

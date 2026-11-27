import os
from pathlib import Path

import cv2
import numpy as np

STEP_S = 0.5
MAX_SPEED = 0.04  # max centre move per sample, as a fraction of frame width (about 8% of width per second)
EMA = 0.3

_CASCADE = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))


def face_centres(path: Path, ranges: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    try:
        times = sorted({round(float(t), 3) for a, b in ranges for t in np.arange(a, b, STEP_S)})
        xs = []
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                xs.append(np.nan)
                continue
            scale = 640 / frame.shape[1]
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), None, fx=scale, fy=scale)
            faces = _CASCADE.detectMultiScale(small, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces):
                x, _, w, _ = max(faces, key=lambda f: f[2] * f[3])
                xs.append((x + w / 2) / small.shape[1])
            else:
                xs.append(np.nan)
        return np.array(times), np.array(xs)
    finally:
        cap.release()


def track_fn(times: np.ndarray, xs: np.ndarray):
    """Smoothed, speed-limited horizontal centre (0..1) as a function of source time."""
    valid = ~np.isnan(xs)
    if not valid.any():
        return lambda t: 0.5
    filled = np.interp(np.arange(len(xs)), np.flatnonzero(valid), xs[valid])
    smooth = np.empty_like(filled)
    smooth[0] = filled[0]
    for i in range(1, len(filled)):
        target = EMA * filled[i] + (1 - EMA) * smooth[i - 1]
        smooth[i] = smooth[i - 1] + np.clip(target - smooth[i - 1], -MAX_SPEED, MAX_SPEED)
    return lambda t: float(np.interp(t, times, smooth))

import re
from dataclasses import dataclass

import numpy as np

WINDOW_LENGTHS = (30, 35, 40, 45, 50, 55)
STEP_S = 5.0
HOOK_LEN_S = 3.0
PAUSE_S = 1.0

# shortcut: English word lists only (campaigns are English for now), add per-language lists when a campaign needs one
NEGATION = {"not", "no", "never", "wrong", "myth", "lie", "lies", "stop", "worst", "bad", "fake",
            "mistake", "quit", "don't", "can't", "won't", "nobody"}
EMOTION = {"amazing", "insane", "crazy", "love", "hate", "shocking", "secret", "truth", "best",
           "money", "free", "scared", "angry", "everyone", "stupid"}


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    text: str
    score: float


def _norm(word: str) -> str:
    return word.lower().strip(".,!?;:\"'()")


def _has_num(word: str) -> bool:
    return any(c.isdigit() for c in word) or "%" in word


def _strong(word: str) -> bool:
    return "?" in word or _has_num(word) or _norm(word) in NEGATION or _norm(word) in EMOTION


def _overlap(a: Window, b: Window) -> float:
    inter = min(a.end, b.end) - max(a.start, b.start)
    if inter <= 0:
        return 0.0
    return inter / min(a.end - a.start, b.end - b.start)


def prefilter(words: list[dict], audio: np.ndarray, sr: int, duration: float, top_n: int) -> list[Window]:
    raw: list[tuple[Window, float]] = []
    for length in WINDOW_LENGTHS:
        for a in np.arange(0, duration, STEP_S):
            b = float(a) + length
            if b > duration:
                break
            ws = [w for w in words if w["start"] >= a and w["end"] <= b]
            if not ws or not any(_strong(w["word"]) for w in ws if w["start"] < a + HOOK_LEN_S):
                continue
            questions = sum("?" in w["word"] for w in ws)
            numbers = sum(_has_num(w["word"]) for w in ws)
            negations = sum(_norm(w["word"]) in NEGATION for w in ws)
            emotions = sum(_norm(w["word"]) in EMOTION for w in ws)
            gaps = sum(1 for p, q in zip(ws, ws[1:]) if q["start"] - p["end"] > PAUSE_S)
            seg = audio[int(a * sr):int(b * sr)]
            rms = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
            score = (min(len(ws) / length, 4) + 1.5 * min(questions, 2) + 1.5 * min(numbers, 2)
                     + 0.75 * min(negations, 3) + 0.75 * min(emotions, 3) - 0.5 * min(gaps, 4))
            raw.append((Window(float(a), b, " ".join(w["word"] for w in ws), score), rms))
    if not raw:
        return []
    max_rms = max(r for _, r in raw) or 1.0
    scored = sorted(
        (Window(w.start, w.end, w.text, w.score + 2 * r / max_rms) for w, r in raw),
        key=lambda w: w.score, reverse=True,
    )
    kept: list[Window] = []
    for w in scored:
        if all(_overlap(w, k) <= 0.5 for k in kept):
            kept.append(w)
        if len(kept) == top_n:
            break
    return kept

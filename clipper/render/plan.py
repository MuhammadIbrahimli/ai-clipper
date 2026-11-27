from dataclasses import dataclass

PAD = 0.065  # 0.13 s of silence survives each cut (0.065 s on each side)
GAP = 0.35  # pauses longer than this are cut
CALLOUT_S = 2.5
ACCENT_EVERY_S = 5.0

PRESETS = {
    "classic": {"size": 72, "bold": 0, "karaoke": False},
    "bold": {"size": 92, "bold": -1, "karaoke": False},
    "karaoke": {"size": 80, "bold": -1, "karaoke": True},
}

Range = tuple[float, float]  # source-time range [start, end)


@dataclass(frozen=True)
class Timeline:
    ranges: list[Range]  # source ranges in output order: teaser hook first, then the jump-cut body
    total: float  # output duration in seconds


def _is_strong(word: str) -> bool:
    return "?" in word or "%" in word or any(c.isdigit() for c in word)


def jump_cut_ranges(words: list[dict], start: float, end: float) -> list[Range]:
    body = [w for w in words if w["start"] >= start and w["end"] <= end]
    if not body:
        return [(start, end)]
    groups = [[body[0]]]
    for p, q in zip(body, body[1:]):
        if q["start"] - p["end"] > GAP:
            groups.append([q])
        else:
            groups[-1].append(q)
    return [(max(start, g[0]["start"] - PAD), min(end, g[-1]["end"] + PAD)) for g in groups]


def build_timeline(words: list[dict], start: float, end: float, hook_start: float, hook_end: float,
                   max_out: float) -> Timeline:
    budget = max_out - (hook_end - hook_start)
    body: list[Range] = []
    used = 0.0
    for a, b in jump_cut_ranges(words, start, end):
        if used >= budget:
            break
        take = min(b - a, budget - used)
        body.append((a, a + take))
        used += take
    ranges = [(hook_start, hook_end)] + body
    return Timeline(ranges, sum(b - a for a, b in ranges))


def out_words(words: list[dict], tl: Timeline) -> list[tuple[float, float, str]]:
    res = []
    off = 0.0
    for a, b in tl.ranges:
        for w in words:
            if w["start"] >= a and w["end"] <= b:
                res.append((off + w["start"] - a, off + w["end"] - a, w["word"]))
        off += b - a
    return sorted(res)


def out_to_src(tl: Timeline, tau: float) -> float:
    off = 0.0
    for a, b in tl.ranges:
        if tau < off + (b - a):
            return a + (tau - off)
        off += b - a
    return tl.ranges[-1][1]


def zoom_accents(words: list[tuple[float, float, str]], total: float) -> list[float]:
    accents = []
    t = ACCENT_EVERY_S / 2
    while t < total - 1:
        near = [s for s, _, w in words if abs(s - t) <= 0.5 and _is_strong(w)]  # +-0.5 s keeps spacing in 4-6 s
        accents.append(min(near, key=lambda s: abs(s - t)) if near else t)
        t += ACCENT_EVERY_S
    return accents


def zoom_at(accents: list[float], tau: float) -> float:
    z = 1.0
    for a in accents:
        d = tau - a
        if -0.4 <= d < 0:
            z = max(z, 1 + 0.12 * (1 + d / 0.4))
        elif 0 <= d < 0.3:
            z = max(z, 1.12)
        elif 0.3 <= d < 0.7:
            z = max(z, 1 + 0.12 * (1 - (d - 0.3) / 0.4))
    return z


def callouts(words: list[tuple[float, float, str]], total: float) -> list[tuple[float, float, str]]:
    res: list[tuple[float, float, str]] = []
    last = -10.0
    for i, (s, _, w) in enumerate(words):
        if _is_strong(w) and s - last >= 4 and s + CALLOUT_S <= total:
            res.append((s, s + CALLOUT_S, " ".join(x[2] for x in words[max(0, i - 2):i + 4])))
            last = s
            if len(res) == 2:
                break
    return res


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    return f"{h}:{m:02d}:{rem // 100:02d}.{rem % 100:02d}"


def ass_subtitles(words: list[tuple[float, float, str]], preset: str, width: int, height: int) -> str:
    p = PRESETS[preset]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        # safe zone: 15% left/right, 25% bottom
        f"Style: Default,Arial,{p['size']},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,{p['bold']},0,0,0,100,100,0,0,1,4,0,2,"
        f"{round(width * 0.15)},{round(width * 0.15)},{round(height * 0.25)},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for i in range(0, len(words), 3):
        chunk = words[i:i + 3]
        if p["karaoke"]:
            text = " ".join(f"{{\\k{round((e - s) * 100)}}}{w.replace('{', '').replace('}', '')}" for s, e, w in chunk)
            lines.append(f"Dialogue: 0,{_ass_time(chunk[0][0])},{_ass_time(chunk[-1][1])},Default,,0,0,0,,{text}")
            continue
        for j, (s, e, _) in enumerate(chunk):
            end = chunk[j + 1][0] if j + 1 < len(chunk) else e
            text = " ".join(
                ("{\\1c&H00FFFF&}" + w.replace("{", "").replace("}", "") + "{\\1c&HFFFFFF&}") if k == j
                else w.replace("{", "").replace("}", "")
                for k, (_, _, w) in enumerate(chunk)
            )
            lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"

import logging
import shutil
import subprocess
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

from clipper.core.config import Campaign, Logo
from clipper.render import plan
from clipper.render.track import face_centres, track_fn

log = logging.getLogger(__name__)

OUT_W, OUT_H, FPS = 1080, 1920, 30
FONT = "arialbd.ttf"  # Windows system font; Pillow resolves it through the system font directories


class RenderError(Exception):
    pass


@dataclass(frozen=True)
class RenderResult:
    path: Path
    duration_s: float
    speed_factor: float
    has_callouts: bool
    zoom_enabled: bool
    took_s: float


def render_frames(src: Path, tl: plan.Timeline, track, accents: list[float], out: Path) -> None:
    cap = cv2.VideoCapture(str(src))
    fps_src = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    crop_w = H * 9 / 16
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-an", str(out)],
        stdin=subprocess.PIPE,
    )
    pos, last_f, frame = 0, -1, None
    try:
        for k in range(round(tl.total * FPS)):
            tau = k / FPS
            t = plan.out_to_src(tl, tau)
            f = min(round(t * fps_src), total_frames - 1)
            if f != last_f:
                if f < pos:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, f)
                    pos = f
                while pos < f:
                    cap.grab()
                    pos += 1
                ok, got = cap.read()
                pos += 1
                if ok:
                    frame = got
                last_f = f
            if frame is None:
                raise RenderError("не удалось прочитать кадры исходника")
            z = plan.zoom_at(accents, tau)
            cw, ch = crop_w / z, H / z
            cx = track(t) * W
            x0 = min(max(cx - cw / 2, 0), W - cw)
            y0 = (H - ch) / 2
            crop = frame[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
            proc.stdin.write(cv2.resize(crop, (OUT_W, OUT_H), interpolation=cv2.INTER_CUBIC).tobytes())
    finally:
        proc.stdin.close()
        code = proc.wait()
        cap.release()
    if code:
        raise RenderError(f"ffmpeg (кадры) завершился с кодом {code}")


def callout_png(text: str, path: Path) -> None:
    font = ImageFont.truetype(FONT, 56)
    lines = textwrap.wrap(text, 24)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    widths = [probe.textbbox((0, 0), ln, font=font)[2] for ln in lines]
    lh = probe.textbbox((0, 0), "Ag", font=font)[3] + 12
    img = Image.new("RGBA", (max(widths) + 80, lh * len(lines) + 40), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=24, fill=(0, 0, 0, 190))
    for i, ln in enumerate(lines):
        draw.text((img.width / 2, 20 + i * lh), ln, font=font, fill=(255, 255, 255, 255), anchor="ma")
    img.save(path)


def logo_png(logo: Logo, path: Path) -> tuple[int, int]:
    img = Image.open(logo.path).convert("RGBA")
    w = 200
    img = img.resize((w, round(img.height * w / img.width)))
    img.putalpha(img.getchannel("A").point(lambda a: int(a * logo.opacity)))
    img.save(path)
    return img.size


def logo_xy(position: str, w: int, h: int) -> tuple[int, int]:
    # safe zone: at least 15% from top, 15% from right, 25% from bottom
    x = round(OUT_W * 0.15) if "left" in position else round(OUT_W * 0.85) - w
    y = round(OUT_H * 0.15) if "top" in position else round(OUT_H * 0.75) - h
    return x, y


def _ffmpeg(args: list[str], cwd: Path) -> None:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=cwd)
    if proc.returncode:
        raise RenderError(f"ffmpeg: {proc.stderr.strip()[-500:]}")


def render_clip(campaign: Campaign, words: list[dict], src: Path, start: float, end: float,
                hook_start: float, hook_end: float, out: Path, workdir: Path) -> RenderResult:
    t0 = time.time()
    workdir = workdir.resolve()  # ffmpeg runs with cwd=workdir, so inputs must be absolute
    workdir.mkdir(parents=True, exist_ok=True)
    tl = plan.build_timeline(words, start, end, hook_start, hook_end, campaign.clip.max_s)
    times, xs = face_centres(src, tl.ranges)
    ow = plan.out_words(words, tl)
    accents = plan.zoom_accents(ow, tl.total)
    cos = plan.callouts(ow, tl.total)

    render_frames(src, tl, track_fn(times, xs), accents, workdir / "video.mp4")
    (workdir / "sub.ass").write_text(plan.ass_subtitles(ow, campaign.subtitle_preset, OUT_W, OUT_H), encoding="utf-8")

    src, out = src.resolve(), out.resolve()
    inputs = ["-i", str(workdir / "video.mp4"), "-i", str(src)]
    n_inputs = 2  # input 0 is the silent video, input 1 the source (audio only)
    filters = ["[0:v]ass=sub.ass[v0]"]
    label = "v0"
    for i, (s, e, text) in enumerate(cos):
        png = workdir / f"callout{i}.png"
        callout_png(text, png)
        inputs += ["-loop", "1", "-i", str(png)]
        nxt = f"v{i + 1}"
        filters.append(f"[{label}][{n_inputs}:v]overlay=x=(W-w)/2:y={round(OUT_H * 0.18)}"
                       f":enable='between(t,{s:.3f},{e:.3f})'[{nxt}]")
        label, n_inputs = nxt, n_inputs + 1
    if campaign.logo:
        lp = workdir / "logo.png"
        x, y = logo_xy(campaign.logo.position, *logo_png(campaign.logo, lp))
        inputs += ["-loop", "1", "-i", str(lp)]
        filters.append(f"[{label}][{n_inputs}:v]overlay=x={x}:y={y}[vout]")
    else:
        filters.append(f"[{label}]null[vout]")

    n = len(tl.ranges)
    for i, (a, b) in enumerate(tl.ranges):
        L = b - a
        filters.append(f"[1:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                       f"afade=t=in:st=0:d=0.02,afade=t=out:st={max(L - 0.02, 0):.3f}:d=0.02[a{i}]")
    filters.append("".join(f"[a{i}]" for i in range(n)) +
                   f"concat=n={n}:v=0:a=1,loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    out.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{tl.total:.3f}", str(out)],
            cwd=workdir)
    shutil.rmtree(workdir)
    took = time.time() - t0
    log.info("рендер %s: %.1f с выход, %.1f с работа", out.name, tl.total, took)
    return RenderResult(out, tl.total, tl.total / (end - start), bool(cos), bool(accents), took)

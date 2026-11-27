import hashlib
import json
import logging
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from faster_whisper.audio import decode_audio
from pydantic import BaseModel, Field, ValidationError

from clipper.core.config import Campaign, Settings
from clipper.select.prefilter import prefilter
from clipper.transcribe.transcribe import transcribe

log = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache/selections")
BATCH_SIZE = 20
SR = 16000


class SelectError(Exception):
    pass


class ClaudeClip(BaseModel):
    start: float
    end: float
    hook_start: float
    hook_end: float
    hook_text: str
    score: int = Field(ge=0, le=100)
    hook_type: Literal["conflict", "contradiction", "shocking_number", "prohibition"]
    reason: str
    title_overlay: str
    description: str
    hashtags: list[str] = []
    mentions: list[str] = []


@dataclass(frozen=True)
class SelectResult:
    campaign: str
    source_sha256: str
    inserted: int
    reason: str | None


PROMPT = """You select viral short-video clips from a transcript for the campaign "{name}".
Campaign brief: language {language}. Required hashtags: {hashtags}. Required mentions: {mentions}.

Rubric (score 0-100):
- A hook in the first 3 seconds of the clip: conflict, contradiction, shocking number or prohibition. No greetings, no intros.
- Strong retention through the whole clip.
- Potential to spark debate in comments.
- Fit to the campaign brief.

Clip length must be {min_s} to {max_s} seconds. Choose the sharpest phrase inside the clip as the teaser hook:
hook_start/hook_end must lie inside the clip, be 2 to 4 seconds long, and hook_text must be the exact transcript text.
hook_type is one of: conflict, contradiction, shocking_number, prohibition.
Use only words present in the transcript.
The description must contain every required hashtag and every required mention exactly as written above (for example #clip), in addition to filling the hashtags and mentions lists.
Return one object for EVERY candidate window, including weak ones: score each one honestly, do not merge or skip windows.
The caller applies the score threshold.

Output ONLY a JSON array (no prose, no code fences). Each object has keys:
start, end, hook_start, hook_end, hook_text, score (integer 0-100), hook_type, reason, title_overlay, description, hashtags (list), mentions (list).

Candidates (time range and transcript):
{candidates}
"""


def build_prompt(campaign: Campaign, batch: list) -> str:
    lines = "\n".join(f"[{w.start:.1f}-{w.end:.1f}] {w.text}" for w in batch)
    return PROMPT.format(
        name=campaign.name, language=campaign.language,
        hashtags=", ".join(campaign.required_hashtags) or "none",
        mentions=", ".join(campaign.required_mentions) or "none",
        min_s=campaign.clip.min_s, max_s=campaign.clip.max_s, candidates=lines,
    )


def ask_claude(prompt: str) -> str:
    exe = shutil.which("claude")
    if exe is None:
        raise SelectError("claude CLI не найден в PATH")
    proc = subprocess.run(
        [exe, "-p", "--output-format", "json"],
        input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=900, check=False,
    )
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise SelectError(f"claude вернул не JSON: {proc.stderr.strip() or proc.stdout[:300]}") from e
    if envelope.get("is_error"):
        raise SelectError(f"claude: {envelope.get('result')}")
    return envelope["result"]


def check_clips(raw_text: str, campaign: Campaign) -> tuple[list[ClaudeClip], list[str]]:
    start, end = raw_text.find("["), raw_text.rfind("]")
    if start < 0 or end < start:
        return [], ["ответ не содержит JSON-массива"]
    try:
        items = json.loads(raw_text[start:end + 1])
    except json.JSONDecodeError as e:
        return [], [f"невалидный JSON: {e}"]
    clips: list[ClaudeClip] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        try:
            c = ClaudeClip.model_validate(item)
        except ValidationError as e:
            errors.append(f"элемент {i}: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
            continue
        length = c.end - c.start
        if not campaign.clip.min_s <= length <= campaign.clip.max_s:
            errors.append(f"элемент {i}: длина клипа {length:.1f} с вне {campaign.clip.min_s}-{campaign.clip.max_s}")
        if not (c.start <= c.hook_start < c.hook_end <= c.end and 2 <= c.hook_end - c.hook_start <= 4):
            errors.append(f"элемент {i}: тизер должен быть внутри клипа и длиться 2-4 с")
        clips.append(c)
    return ([] if errors else clips), errors


def select_batch(campaign: Campaign, batch: list, calls: list[int], conn: sqlite3.Connection,
                 daily_limit: int) -> list[ClaudeClip]:
    # calls[0] counts Claude requests for this source; a list keeps the counter shared across batches
    prompt = build_prompt(campaign, batch)
    key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    cache = CACHE_DIR / f"{key}.json"
    if cache.exists():
        return [ClaudeClip.model_validate(c) for c in json.loads(cache.read_text(encoding="utf-8"))]

    retry_note = ""
    for attempt in range(2):
        if calls[0] >= campaign.limits.max_claude_requests_per_source:
            log.warning("лимит max_claude_requests_per_source=%d исчерпан, батч пропущен", campaign.limits.max_claude_requests_per_source)
            return []
        used = conn.execute("SELECT COUNT(*) FROM claude_calls WHERE created_at >= datetime('now', '-1 day')").fetchone()[0]
        if used >= daily_limit:
            log.warning("суточный лимит claude_daily_limit=%d исчерпан, батч пропущен", daily_limit)
            return []
        calls[0] += 1
        with conn:
            conn.execute("INSERT INTO claude_calls DEFAULT VALUES")
        raw = ask_claude(prompt + retry_note)
        clips, errors = check_clips(raw, campaign)
        if not errors:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps([c.model_dump() for c in clips], ensure_ascii=False), encoding="utf-8")
            return clips
        log.warning("ответ Claude отклонён (попытка %d): %s", attempt + 1, "; ".join(errors))
        retry_note = ("\n\nYour previous answer was rejected: " + "; ".join(errors)
                      + "\nReturn the corrected JSON array only.")
    return []


def select_source(campaign: Campaign, sha256: str, path: Path, settings: Settings, conn: sqlite3.Connection) -> SelectResult:
    transcript = transcribe(path, settings.whisper_model)
    words = [w for s in transcript["segments"] for w in s["words"]]
    duration = transcript["duration_s"]
    if not words:
        return SelectResult(campaign.id, sha256, 0, "в транскрипте нет речи")
    if duration < campaign.clip.min_s:
        return SelectResult(campaign.id, sha256, 0, f"видео короче {campaign.clip.min_s:g} с")

    audio = decode_audio(str(path), sampling_rate=SR)
    cands = prefilter(words, audio, SR, duration, settings.prefilter_top_n)
    if not cands:
        return SelectResult(campaign.id, sha256, 0, "префильтр не нашёл окон с сильным словом в первые 3 с")

    calls = [0]
    clips: list[ClaudeClip] = []
    for i in range(0, len(cands), BATCH_SIZE):
        clips += select_batch(campaign, cands[i:i + BATCH_SIZE], calls, conn, settings.claude_daily_limit)
    passed = sorted((c for c in clips if c.score >= campaign.clip.min_score), key=lambda c: c.score, reverse=True)
    passed = passed[:campaign.clip.max_clips_per_source]
    if not passed:
        return SelectResult(campaign.id, sha256, 0, f"Claude не дал клипов с score >= {campaign.clip.min_score}")

    inserted = 0
    with conn:
        for c in passed:
            cur = conn.execute(
                "INSERT OR IGNORE INTO clips (campaign, source_sha256, start, end, hook_start, hook_end, hook_text,"
                " hook_type, score, reason, title_overlay, description, hashtags, mentions, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign.id, sha256, c.start, c.end, c.hook_start, c.hook_end, c.hook_text, c.hook_type,
                 c.score, c.reason, c.title_overlay, c.description, json.dumps(c.hashtags, ensure_ascii=False),
                 json.dumps(c.mentions, ensure_ascii=False), "selected"),
            )
            inserted += cur.rowcount
    return SelectResult(campaign.id, sha256, inserted, None)

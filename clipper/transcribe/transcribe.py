import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from faster_whisper import WhisperModel

from clipper.ingest.scan import sha256_file

log = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache/transcripts")


@lru_cache(maxsize=1)
def _model(name: str) -> WhisperModel:
    log.info("загрузка модели whisper %s (cpu, int8)", name)
    return WhisperModel(name, device="cpu", compute_type="int8")


def transcribe(path: Path, model_name: str = "small", cache_dir: Path = CACHE_DIR) -> dict:
    digest = sha256_file(path)
    cache = cache_dir / f"{digest}-{model_name}.json"
    if cache.exists():
        log.info("транскрипт из кэша: %s", cache)
        return json.loads(cache.read_text(encoding="utf-8"))

    segments, info = _model(model_name).transcribe(str(path), vad_filter=True, word_timestamps=True)
    result = {
        "source_sha256": digest,
        "model": model_name,
        "language": info.language,
        "duration_s": info.duration,
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text.strip(),
                "words": [
                    {"start": w.start, "end": w.end, "word": w.word.strip(), "probability": w.probability}
                    for w in (s.words or [])
                ],
            }
            for s in segments
        ],
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cache)
    log.info("транскрипт сохранён: %s", cache)
    return result

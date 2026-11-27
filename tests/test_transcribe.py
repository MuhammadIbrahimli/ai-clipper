import pytest

from clipper.transcribe import transcribe as tmod


def test_transcribe_writes_schema_and_cache(tmp_path, test_video):
    result = tmod.transcribe(test_video, "tiny", cache_dir=tmp_path)
    assert result["model"] == "tiny"
    assert set(result) >= {"source_sha256", "language", "duration_s", "segments"}
    assert list(tmp_path.glob("*-tiny.json"))


def test_second_run_is_served_from_cache(tmp_path, test_video, monkeypatch):
    first = tmod.transcribe(test_video, "tiny", cache_dir=tmp_path)

    def no_model(name):
        raise AssertionError("модель не должна загружаться при попадании в кэш")

    monkeypatch.setattr(tmod, "_model", no_model)
    assert tmod.transcribe(test_video, "tiny", cache_dir=tmp_path) == first


@pytest.mark.parametrize("bad", ["huge", "large"])
def test_unknown_model_rejected(tmp_path, bad):
    from clipper.core.config import ConfigError, load_settings

    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"whisper_model: {bad}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="поле 'whisper_model'"):
        load_settings(cfg)


def test_default_settings_without_file(tmp_path):
    from clipper.core.config import load_settings

    assert load_settings(tmp_path / "missing.yaml").whisper_model == "small"

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from clipper.core.config import AndroidApp, TargetAccount
from clipper.publish import android
from clipper.publish.android import AndroidAdapter
from clipper.publish.base import ClipInfo, PublishError

STEPS = ("create", "gallery", "pick", "next", "description", "publish")
CLIP = ClipInfo(7, Path("out/7.mp4"), "title", "desc #clip", ["#clip"], [])
ACCOUNT = TargetAccount(platform="tiktok", account_label="main", device_id="SERIAL1")


class FakeEl:
    def __init__(self, log, name, found):
        self.log, self.name, self.found = log, name, found

    def wait(self, timeout):
        return self.found

    def click(self):
        self.log.append(("click", self.name))
        return self

    def set_text(self, text):
        self.log.append(("text", text))


class FakeDevice:
    def __init__(self, log, missing=()):
        self.log, self.missing = log, set(missing)

    def __call__(self, **selector):
        name = selector["text"]
        return FakeEl(self.log, name, name not in self.missing)

    def shell(self, cmd):
        self.log.append(("shell", cmd))


@pytest.fixture
def app_cfg():
    return AndroidApp(package="com.example.app", user=10, selectors={s: {"text": s} for s in STEPS})


@pytest.fixture
def adb(monkeypatch):
    cmds: list[list[str]] = []

    def run(cmd, **kwargs):
        cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(android.subprocess, "run", run)
    return cmds


def test_app_config_requires_every_step():
    with pytest.raises(ValidationError, match="publish"):
        AndroidApp(package="p", selectors={s: {"text": s} for s in STEPS[:-1]})


def test_publish_taps_steps_in_order_and_cleans_up(app_cfg, adb):
    log: list = []
    adapter = AndroidAdapter("tiktok", app_cfg, "adb", connect=lambda serial: FakeDevice(log))
    assert adapter.publish(CLIP, ACCOUNT) == "android:SERIAL1:7"
    assert adb[0] == ["adb", "-s", "SERIAL1", "push", str(Path("out/7.mp4")), "/sdcard/Movies/clipper_7.mp4"]
    assert log[0] == ("shell", "monkey -u 10 -p com.example.app -c android.intent.category.LAUNCHER 1")
    assert [x for x in log[1:]] == [
        ("click", "create"), ("click", "gallery"), ("click", "pick"), ("click", "next"),
        ("text", "desc #clip"), ("click", "publish"),
    ]
    assert adb[-1] == ["adb", "-s", "SERIAL1", "shell", "rm", "-f", "/sdcard/Movies/clipper_7.mp4"]


def test_missing_element_fails_and_still_removes_file(app_cfg, adb):
    log: list = []
    adapter = AndroidAdapter("tiktok", app_cfg, "adb", connect=lambda s: FakeDevice(log, missing={"gallery"}))
    with pytest.raises(PublishError, match="gallery"):
        adapter.publish(CLIP, ACCOUNT)
    assert "rm" in adb[-1] and ("click", "publish") not in log


def test_push_failure_stops_before_device(app_cfg, monkeypatch):
    monkeypatch.setattr(android.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "device offline"))
    adapter = AndroidAdapter("tiktok", app_cfg, "adb", connect=lambda s: pytest.fail("устройство не должно открываться"))
    with pytest.raises(PublishError, match="device offline"):
        adapter.publish(CLIP, ACCOUNT)


def test_account_without_serial_is_rejected(app_cfg):
    adapter = AndroidAdapter("tiktok", app_cfg, "adb")
    with pytest.raises(PublishError, match="device_id"):
        adapter.publish(CLIP, TargetAccount(platform="tiktok", account_label="main"))


def test_settings_android_block_parses_from_yaml(tmp_path):
    from clipper.core.config import ConfigError, load_settings

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "adb_path: 'tools\\platform-tools\\adb.exe'\n"
        "android_apps:\n  tiktok:\n    package: com.example\n    user: 0\n    selectors:\n"
        + "".join(f"      {s}: {{text: {s}}}\n" for s in STEPS),
        encoding="utf-8")
    s = load_settings(cfg)
    assert s.adb_path == "tools\\platform-tools\\adb.exe" and s.android_apps["tiktok"].package == "com.example"
    cfg.write_text("android_apps:\n  tiktok:\n    package: p\n    selectors: {create: {text: x}}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="не хватает шагов"):
        load_settings(cfg)

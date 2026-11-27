import logging
import subprocess
from typing import Any, Callable

import uiautomator2 as u2

from clipper.core.config import AndroidApp, TargetAccount
from clipper.publish.base import ClipInfo, PublishError

log = logging.getLogger(__name__)

REMOTE_DIR = "/sdcard/Movies"
WAIT_S = 15


class AndroidAdapter:
    """Publishes through the app UI on a USB-connected phone. Used where no official upload API is available (TikTok)."""

    def __init__(self, platform: str, app: AndroidApp, adb: str,
                 connect: Callable[[str], Any] = u2.connect):
        self.platform = platform
        self.app = app
        self.adb = adb
        self.connect = connect

    def _tap(self, d: Any, step: str) -> Any:
        el = d(**self.app.selectors[step])
        if not el.wait(timeout=WAIT_S):
            raise PublishError(f"экран приложения: не найден элемент шага '{step}' {self.app.selectors[step]}")
        return el

    def publish(self, clip: ClipInfo, account: TargetAccount) -> str:
        serial = account.device_id
        if not serial:
            raise PublishError("для android-публикации в target_accounts нужен device_id (serial из adb devices)")
        remote = f"{REMOTE_DIR}/clipper_{clip.id}.mp4"
        push = subprocess.run([self.adb, "-s", serial, "push", str(clip.path), remote],
                              capture_output=True, text=True, check=False)
        if push.returncode != 0:
            raise PublishError(f"adb push: {push.stderr.strip() or push.stdout.strip()}")
        try:
            d = self.connect(serial)
            d.shell(f"monkey -u {self.app.user} -p {self.app.package} -c android.intent.category.LAUNCHER 1")
            for step in ("create", "gallery", "pick", "next"):
                self._tap(d, step).click()
            self._tap(d, "description").set_text(clip.description)
            self._tap(d, "publish").click()
        except PublishError:
            raise
        except Exception as e:  # uiautomator2 and adb raise several unrelated types
            raise PublishError(f"android ({serial}): {e}") from e
        finally:
            subprocess.run([self.adb, "-s", serial, "shell", "rm", "-f", remote], capture_output=True, check=False)
        return f"android:{serial}:{clip.id}"

from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from clipper.core.config import TargetAccount
from clipper.publish.base import ClipInfo, PublishError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_DIR = Path("data/tokens")
# Uploads stay private until someone checks the first video and switches this to "public".
PRIVACY = "private"


def token_path(label: str) -> Path:
    return TOKEN_DIR / f"youtube_{label}.json"


def authorize(client_secret: Path, label: str) -> None:
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    token_path(label).write_text(creds.to_json(), encoding="utf-8")


class YouTubeAdapter:
    platform = "youtube"

    def publish(self, clip: ClipInfo, account: TargetAccount) -> str:
        path = token_path(account.account_label)
        if not path.exists():
            raise PublishError(f"нет токена {path}: выполните youtube-auth")
        try:
            creds = Credentials.from_authorized_user_file(str(path), SCOPES)
            if not creds.valid:
                if not (creds.expired and creds.refresh_token):
                    raise PublishError("токен недействителен: повторите youtube-auth")
                creds.refresh(Request())
                path.write_text(creds.to_json(), encoding="utf-8")
            yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
            description = clip.description
            if "#shorts" not in description.lower():
                description = f"{description} #Shorts".strip()
            body = {
                "snippet": {
                    "title": clip.title[:100],
                    "description": description,
                    "tags": [h.lstrip("#") for h in clip.hashtags][:15],
                },
                "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
            }
            media = MediaFileUpload(str(clip.path), mimetype="video/mp4", resumable=True)
            request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                _, response = request.next_chunk()
            return response["id"]
        except HttpError as e:
            raise PublishError(f"YouTube API: {e.reason}") from e
        except (GoogleAuthError, OSError) as e:
            raise PublishError(f"YouTube: {e}") from e

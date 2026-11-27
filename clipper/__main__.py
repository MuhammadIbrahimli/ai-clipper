import argparse
import logging
import os
import sys
from pathlib import Path

from clipper import pipeline
from clipper.core.config import ConfigError, load_campaign, load_settings
from clipper.core.db import connect
from clipper.ingest.scan import IngestError
from clipper.publish.youtube import authorize, token_path
from clipper.select.select import SelectError
from clipper.transcribe.transcribe import transcribe

log = logging.getLogger(__name__)


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    load_dotenv()
    parser = argparse.ArgumentParser(prog="clipper")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text in [
        ("ingest", "зарегистрировать исходники кампании"),
        ("select", "отобрать клипы для зарегистрированных исходников"),
        ("render", "отрендерить отобранные клипы в 9:16"),
        ("gate", "проверить отрендеренные клипы: ready или rejected с причиной"),
        ("publish", "поставить approved клипы в очередь и опубликовать то, что пришло по расписанию"),
        ("process", "ingest, select, render и gate подряд"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("campaign", type=Path, help="путь к campaigns/<id>.yaml")
        p.add_argument("--config", type=Path, default=Path("config.yaml"))
        p.add_argument("--db", type=Path, default=Path("data/clipper.db"))
    tr = sub.add_parser("transcribe", help="транскрибировать видео (с кэшем)")
    tr.add_argument("video", type=Path)
    tr.add_argument("--config", type=Path, default=Path("config.yaml"))
    yta = sub.add_parser("youtube-auth", help="получить OAuth-токен YouTube для аккаунта")
    yta.add_argument("account_label", help="account_label из campaign.target_accounts")
    yta.add_argument("client_secret", type=Path, nargs="?", default=os.environ.get("CLIPPER_YT_CLIENT_SECRET"),
                     help="client_secret.json из Google Cloud Console (или CLIPPER_YT_CLIENT_SECRET в .env)")
    srv = sub.add_parser("serve", help="запустить веб-интерфейс на localhost")
    srv.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.cmd == "transcribe":
            settings = load_settings(args.config)
            result = transcribe(args.video, settings.whisper_model)
            words = sum(len(s["words"]) for s in result["segments"])
            print(
                f"{args.video}: модель {result['model']}, язык {result['language']}, "
                f"длительность {result['duration_s']:.1f} с, сегментов {len(result['segments'])}, слов {words}"
            )
        elif args.cmd == "youtube-auth":
            if args.client_secret is None:
                print("ошибка: укажите client_secret.json или CLIPPER_YT_CLIENT_SECRET в .env", file=sys.stderr)
                return 1
            authorize(Path(args.client_secret), args.account_label)
            print(f"токен сохранён: {token_path(args.account_label)}")
        elif args.cmd == "serve":
            import uvicorn
            from clipper.web.app import create_app
            uvicorn.run(create_app(Path(".")), host="127.0.0.1", port=args.port)
        else:
            campaign = load_campaign(args.campaign)
            settings = load_settings(args.config)
            pipeline.apply_tool_paths(settings)
            conn = connect(args.db)
            say = print
            if args.cmd == "ingest":
                pipeline.ingest_step(campaign, conn, say)
            elif args.cmd == "select":
                pipeline.select_step(campaign, settings, conn, say)
            elif args.cmd == "render":
                pipeline.render_step(campaign, settings, conn, say)
            elif args.cmd == "gate":
                pipeline.gate_step(campaign, conn, say)
            elif args.cmd == "publish":
                pipeline.publish_step(campaign, settings, conn, say)
            elif args.cmd == "process":
                pipeline.ingest_step(campaign, conn, say)
                pipeline.select_step(campaign, settings, conn, say)
                pipeline.render_step(campaign, settings, conn, say)
                pipeline.gate_step(campaign, conn, say)
    except (ConfigError, IngestError, SelectError) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

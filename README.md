# Clipper

A local Windows app for short-form clipping. It takes a campaign's source videos, finds the strongest moments, renders vertical 9:16 clips (30–55 s), and queues them for publishing. Everything runs on your own machine (CPU only). State lives in SQLite (`data/clipper.db`).

## Pipeline

1. **Ingest** – register source files and folders, probe them with ffprobe, hash them (SHA-256) so re-registration is idempotent.
2. **Transcribe** – faster-whisper (`small` by default, int8, VAD, word timestamps). Transcripts are cached per source hash.
3. **Select** – a local prefilter scores sliding 30–55 s windows (speech density, pauses, questions, numbers, negations, emotional words, loudness). The top candidates go to Claude through the Claude Code CLI, which returns scored clips with a teaser hook. Responses are validated with pydantic and retried once on error.
4. **Render** – 9:16 crop that follows the face (OpenCV Haar cascade, smoothed and speed-limited), zoom accents, jump-cuts on long pauses, ASS subtitles, Pillow callout cards, optional logo in a safe zone, loudness normalisation to −14 LUFS, H.264 output via ffmpeg.
5. **Gate** – duration, audio, black frames (`blackdetect`), teaser present, required hashtags and mentions in the description. Failing clips are `rejected` with a reason.
6. **Approve and publish** – you approve clips in the UI. A scheduler publishes approved clips with random spacing (`min_gap_minutes`), a daily cap (`per_day`), retries, and restart safety.

## Requirements

- Windows 11, Python 3.13 (`setup.bat` looks for `C:\laragon\bin\python\python-3.13\python.exe`, then `py -3.13`).
- **ffmpeg and ffprobe** on `PATH`, or a full path set as `ffmpeg_path` in `config.yaml`.
- **Claude Code CLI** (`claude`), signed in with your subscription: `claude auth login`.
- **Node.js LTS** – only needed to build the web UI.
- For Android publishing: **Android platform-tools** (`adb`). Put them in `tools\platform-tools\` or set `adb_path`.
- Whisper models download automatically on first transcription (`small` is about 0.5 GB).

## Install and run

```bat
setup.bat
run.bat
```

`setup.bat` creates `.venv`, installs Python dependencies, checks ffmpeg, adb, the Claude CLI and the Whisper model (missing items are reported as warnings), and builds the web UI. `run.bat` starts the server on `http://127.0.0.1:8000` and opens the page in your browser.

## Configuration

- `config.yaml` – Whisper model, ffmpeg and adb paths, Claude request limit per 24 hours. The same settings are editable on the **Settings** page.
- `.env` (git-ignored; template in `.env.example`) – secrets. Currently only the path to the YouTube `client_secret.json`.

## Campaigns

Create a campaign on the **Campaigns** page, or copy `campaigns/example.yaml` to `campaigns/<id>.yaml`. Required fields: `id`, `name`, `mode`, `sources`. Config errors are shown with the offending field name.

Typical workflow:

1. **Sources** – upload video files, or paste a path to a file or folder on this computer.
2. **Process** (button on the Campaigns page) – ingest, transcribe, select with Claude, render, gate. Progress appears in the Jobs panel at the bottom. Re-running uses the caches.
3. **Clip queue** – watch each clip, edit title, description, hashtags and subtitle preset. Approve clips that are `ready`. "Save and re-render" rebuilds the video. Rejected clips are never published.
4. **Publishing** – approved clips go out on the schedule, respecting `per_day` and `min_gap_minutes`. "Publish now" moves the first queued item of each account to the current time; the limits still apply.
5. **Analytics** – enter views, likes and comments by hand, or import a CSV with the header `publication_id,views,likes,comments`. The table groups results by hook type, subtitle preset, speed factor and other attributes.

## YouTube Shorts (official API)

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **YouTube Data API v3**.
2. Create an OAuth client ID of type **Desktop app** and download `client_secret.json`.
3. Put its path in `.env` as `CLIPPER_YT_CLIENT_SECRET`, then run:

```bat
.venv\Scripts\python.exe -m clipper youtube-auth main
```

   The label `main` must match `account_label` in the campaign. A browser opens for sign-in; the token is saved to `data/tokens/youtube_main.json`.

Uploads are **private** by default (the `PRIVACY` constant in `clipper/publish/youtube.py`). Switch to public only after checking the first video. The Data API has a daily quota; when it runs out, uploads fail and the clip is retried later.

## Instagram Reels – not implemented

The Graph API requires a Business or Creator account and a **public URL** for the video file, which a local app does not have. This path is skipped: queued Instagram items get the message "platform adapter not implemented".

## TikTok and Android (via the phone app)

TikTok's official Content Posting API only works for approved apps. Without approval, publishing goes through the TikTok app on your own phone and account, driven by ADB and uiautomator2.

1. On the phone, enable Developer options → **USB debugging**, connect the cable and accept the prompt.
2. Check the connection with `tools\platform-tools\adb.exe devices`. Use the serial number (for example `<device-serial>`) as `device_id`.
3. Add the account to the campaign with `platform: tiktok`, `account_label: phone1`, `device_id: <device-serial>`.
4. Fill in `android_apps.tiktok` in `config.yaml`: package name, user profile and the step selectors. Selectors are uiautomator2 keyword arguments such as `{text: "Next"}` or `{description: "Create"}`. The steps are `create`, `gallery`, `pick`, `next`, `description` and `publish`. `adb shell uiautomator dump` helps find the right values on a given screen.

```yaml
android_apps:
  tiktok:
    package: com.zhiliaoapp.musically   # check the package name on your phone
    user: 0
    selectors:
      create: {description: "Create"}
      gallery: {text: "Upload"}
      pick: {className: "android.widget.ImageView", instance: 0}
      next: {text: "Next"}
      description: {className: "android.widget.EditText"}
      publish: {text: "Post"}
```

The adapter pushes the file to `/sdcard/Movies/clipper_<id>.mp4`, walks through the steps and taps publish. The temporary file is removed from the phone in every case. Selectors depend on the app version and break when the app is updated.

## Tests

```bat
.venv\Scripts\python.exe -m pytest
```

Test videos are generated with ffmpeg. Claude and the platforms are mocked.

## What is tested and what is not

- **Covered by automated tests:** config validation, ingestion, transcription cache, prefilter, parsing of Claude responses (mocked), rendering and the quality gate on a generated test video, the publishing queue (idempotency, restart, limits, retries), the Android adapter (with a fake device), the web API, and an "approve → publish → metrics" scenario on a mock adapter.
- **Verified on a real device:** `adb push`, launching an app via `monkey`, opening the YouTube upload screen.
- **Not verified:** real uploads to a YouTube channel (needs your `client_secret.json`), publishing through the TikTok app on a phone (UI steps depend on app version and selectors), Instagram (not implemented).

## Project layout

```
clipper/
  core/        config models (pydantic), SQLite schema
  ingest/      source registration, ffprobe, SHA-256
  transcribe/  faster-whisper with cache
  select/      prefilter and Claude CLI selection
  render/      crop, subtitles, jump-cut, ffmpeg, quality gate
  publish/     YouTube, Android, scheduler
  web/         FastAPI API and background worker
  pipeline.py  processing steps shared by the CLI and the web app
web/           React + Vite + TypeScript UI
campaigns/     campaign configs
data/          database, caches, sources, tokens (git-ignored)
output/        rendered clips (git-ignored)
tests/         pytest suite
```

## Troubleshooting the UI build

On machines where Windows Application Control blocks native binaries, Rollup cannot load its `.node` file. `web/package.json` therefore overrides `rollup` with `@rollup/wasm-node`. It behaves the same, with a slightly slower build.

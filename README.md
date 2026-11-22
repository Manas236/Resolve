# Resolve operator guide

Resolve builds English-language faceless gym motivation reels. The default audience is recreational lifters working on consistent strength-training habits. The local dashboard, media renderer and dry-run posting path are available. The provider gate, target libraries and live integrations are still open. Keep live posting disabled during setup.

## Local setup

Use Python 3.11 or newer, FFmpeg and ffprobe. Check that FFmpeg has libx264, AAC and the ASS filter before rendering. Start from the repository root.

```sh
python3 -m resolve_pipeline
python3 -m unittest discover -s tests -v
```

Copy `.env.example` to `.env` and use one literal `KEY=value` per line. Process environment values take precedence. Generate the scrypt dashboard password hash locally. Paste the single output line into `.env`. The password itself goes only into the login form.

```sh
python3 -c 'from getpass import getpass; from secrets import token_bytes; from resolve_pipeline.dashboard_auth import password_hash; salt=token_bytes(16); print("RESOLVE_DASHBOARD_PASSWORD_HASH="+salt.hex()+"$"+password_hash(getpass(),salt))'
```

Start the local-only dashboard with a clip and a song at least 22 seconds long for F2. Adjust paths to match your machine.

```sh
python3 -m resolve_pipeline.dashboard research/m0_server_results.json --source media/clip.mp4 --song media/song.mp3 --hook "Start with one rep" --quote "Earn the next rep."
```

Open <http://127.0.0.1:8765/>. Use an SSH tunnel when the dashboard runs on AnalyticsDashboard. The app listens on loopback. Each login replaces the existing session, sessions expire after eight hours, and five login failures trigger a one-minute pause.

For F1, provide `--whisper-model` pointing to a local faster-whisper model directory. F1 also needs speech in the source and a sentence boundary in the closing window. For F3, add `--voice-script` and `--piper-model` pointing to a local ONNX model with its adjacent JSON configuration. Install the optional `faster-whisper` and `piper-tts` packages in your runtime environment. Use concise scripts that fit the reel. Repeat `--source` or `--song` to seed more assets. Without a hook, Auto uses the local preview renderer.

## Dashboard controls

| Control | What happens |
| --- | --- |
| Make N reels now | Renders 1–20 reels immediately. Select Auto or F1–F3. Auto uses the operator weights and available format performance when the format renderer is configured. |
| Make reel from URL | Sends the URL to the configured downloader interface. The default installation reports that no adapter can download the source. |
| Approve / Reject | Changes local review state. Live approval mode also requires the Telegram approval record. |
| Regenerate / Delete | Creates a separate replacement with the original render inputs or removes an eligible reel from the review view. |
| Save posting order | Enter each approved reel ID once, separated by commas, in the order you want. |
| Save caption and hashtags | Persists edits to the individual reel. |
| Preview, pin, retire | Previews a library file, prioritizes an asset, or removes it from future automatic selection. |
| Detect timestamp | Runs local FFmpeg energy detection and records the resulting offset. This is a local fallback. |
| Enable / Disable site | Persists a provider preference. Report counts come from the selected probe report. |
| Write retest job | Writes a bounded probe job to the selected job file. Publishing that file to main and a running Lightsail probe worker are required for a remote run. |
| Save settings | Persists scheduling slots, volume, approval mode, refresh cadence and format weights. |
| Start / Stop runner | Starts or stops the dashboard's local render worker. Stop lets the active render finish. |
| Logs and failures | Lists recent dashboard actions, refresh outcomes and render failures. |
| Performance | Displays the ingested 24-hour view summaries by reel, song, clip and format. Optional fixtures are labeled. |
| Log out | Invalidates the dashboard session. |

Routine control submissions update the pipeline section in place. The HTTP integration suite exercises authenticated controls with the worker on and off. Interactive browser acceptance remains pending.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| Posting slots | `09:00,18:00` | Distinct 24-hour clock slots. The scheduler uses its configured timezone, UTC by default. |
| Daily volume | `2` | Number of reserved posting slots per day, bounded by the slots provided. |
| Approval mode | `approval` | Telegram approval is required in week one. An explicit `auto` switch can take effect after that. |
| Refresh cadence | `weekly` | `weekly` or `monthly` for the library and site-health refresh jobs. |
| Format weights | `1,1,1` | Relative F1, F2 and F3 preferences. Zero disables a format in Auto. |

| Environment key | Purpose |
| --- | --- |
| `RESOLVE_STATE_DIR` | Startup runtime directories. Defaults to `state`, relative to the env file. |
| `RESOLVE_LOG_LEVEL` | Console logging threshold. Defaults to `INFO`. |
| `RESOLVE_AUDIENCE` | Configurable audience description for project tooling. |
| `RESOLVE_DASHBOARD_PASSWORD_HASH` | Required local dashboard scrypt value in `salt_hex$digest_hex` form. |
| `GEMINI_API_KEY` | Optional exported key for the explicit billing-disabled M0 Gemini probe. |
| `TELEGRAM_BOT_TOKEN` | Dedicated bot token for the pending live transport setup. |
| `TELEGRAM_CHAT_ID` | The single allowed chat in the live transport setup. |
| `TELEGRAM_REVIEWER_ID` | The single allowed reviewer in the live transport setup. |
| `META_ACCESS_TOKEN` | Token for the dedicated Resolve Meta app and its connected assets. |
| `META_GRAPH_VERSION` | Supported Graph version for that app. |
| `META_IG_USER_ID` | Connected Instagram professional account ID. |
| `META_PAGE_ID` | Connected Facebook Page ID. |
| `META_DRY_RUN` | Keep `true` during setup. The Meta adapter defaults to dry-run and requires explicit live construction. |

The dashboard CLI also accepts `--port` (8765), `--state` (`state/dashboard`), `--job-file` (`jobs/next_job.json`), and `--performance-fixture` (optional JSON). The required positional report is the probe JSON shown in the registry. The operator runner reads Telegram and Meta settings from this same environment file.

## Operations and pending setup

Run `python3 tools/healthcheck.py` from the checkout. It reports FFmpeg, ffprobe, sandboxed Chromium and the selected registry evidence. On AnalyticsDashboard follow the separate profile and health commands in [NEEDS_MANAS.md](NEEDS_MANAS.md).

The dry-run schedule creates independent Instagram and Facebook receipts without network calls. An uncertain live attempt must be reconciled before any retry. Use the same state and clip paths as the dashboard for a local dry-run tick:

```sh
python3 -m resolve_pipeline.operator --once --source media/clip.mp4
```

Omit `--once` to run the dispatcher continuously. `--zone` sets its posting timezone. The service is dry-run unless both `--live` and `META_DRY_RUN=false` are present. Live mode also requires an HTTPS `--media-base` and a configured Meta app. The separately enabled `--telegram` mode sends pending previews and handles callbacks. It uses the configured review chat and reviewer. Start only after the live integration steps in NEEDS_MANAS.md have been verified.

The next Lightsail diagnostic is already queued in `jobs/next_job.json`. See [NEEDS_MANAS.md](NEEDS_MANAS.md) for the dedicated runner, report handoff, competitor evidence, Telegram and Meta setup, channel name and deployment decisions. The current server measurements and tested routes are in [RESEARCH.md](RESEARCH.md). Milestone evidence and exact resume steps are in [PROGRESS.md](PROGRESS.md) and [DECISIONS.md](DECISIONS.md). Keep downloaded media, generated media, secrets and state outside Git.

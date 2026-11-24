# Resolve operator guide

Resolve builds English-language faceless gym motivation reels. The default audience is recreational lifters working on consistent strength-training habits. The local dashboard, media renderer and dry-run posting path are available. The provider gate, target libraries and live integrations are still open. Keep live posting disabled during setup.

## Local setup

Use Python 3.11 or newer, FFmpeg and ffprobe. Check that FFmpeg has libx264, AAC and the ASS filter before rendering. Start from the repository root.

```sh
python3 -m pip install -r requirements.txt
python3 -m resolve_pipeline
python3 -m unittest discover -s tests -v
```

Copy `.env.example` to `.env` and use one literal `KEY=value` per line. Process environment values take precedence. Generate the scrypt dashboard password hash locally. Paste the single output line into `.env`. The password itself goes only into the login form.

```sh
python3 -c 'from getpass import getpass; from secrets import token_bytes; from resolve_pipeline.dashboard_auth import password_hash; salt=token_bytes(16); print("RESOLVE_DASHBOARD_PASSWORD_HASH="+salt.hex()+"$"+password_hash(getpass(),salt))'
```

Start the local-only dashboard with a clip and a song for a poetic Review sample. Adjust paths to match your machine.

```sh
python3 -m resolve_pipeline.dashboard research/m0_server_results.json --source media/clip.mp4 --song media/song.mp3
```

Open <http://127.0.0.1:8765/>. Use an SSH tunnel when the dashboard runs on AnalyticsDashboard. The app listens on loopback. Each login replaces the existing session, sessions expire after eight hours, and five login failures trigger a one-minute pause.

For F1, provide a separate real speech recording with `--speech` and a local faster-whisper directory with `--whisper-model`. P, L and F4 need no speech engine.

## Dashboard controls

| Control | What happens |
| --- | --- |
| Make N reels now | Renders 1–20 reels immediately. Select Auto, F1, P, L, F4 or a manual legacy format. Auto uses the operator weights and available format performance when the format renderer is configured. |
| Make reel from URL | Sends the URL to the configured downloader interface. The default installation reports that no adapter can download the source. |
| Approve / Reject | Changes local review state. Live approval mode also requires the Telegram approval record. |
| Regenerate / Delete | Creates a separate replacement with the original render inputs or removes an eligible reel from the review view. |
| Save posting order | Drag cards or use their move buttons, then save the order. |
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
| Format weights | `3,0,0,1,4,4` | Relative F1, F2, F3, F4, P and L preferences. F2 and F3 are retired from Auto. Zero disables a format in Auto. |

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

The dashboard uses local CSS and JavaScript. Browser theme preferences stay on the device. Run the dashboard with the same `--zone` as the operator to display posting slots in the correct timezone.

The dashboard CLI also accepts `--port` (8765), `--state` (`state/dashboard`), `--job-file` (`jobs/next_job.json`), and `--performance-fixture` (optional JSON). The required positional report is the probe JSON shown in the registry. The operator runner reads Telegram and Meta settings from this same environment file.

## Operations and pending setup

Run `python3 tools/healthcheck.py` from the checkout. It reports FFmpeg, ffprobe, sandboxed Chromium and the selected registry evidence. On AnalyticsDashboard follow the separate profile and health commands in [NEEDS_MANAS.md](NEEDS_MANAS.md).

The dry-run schedule creates independent Instagram and Facebook receipts without network calls. An uncertain live attempt must be reconciled before any retry. Use the same state and clip paths as the dashboard for a local dry-run tick:

```sh
python3 -m resolve_pipeline.operator --once --source media/clip.mp4
```

Omit `--once` to run the dispatcher continuously. `--zone` sets its posting timezone. The service is dry-run unless both `--live` and `META_DRY_RUN=false` are present. With a configured Meta app, the operator uploads the local rendered MP4. Supplying an HTTPS `--media-base` selects the hosted URL workflow. The separately enabled `--telegram` mode sends pending previews and handles callbacks. It uses the configured review chat and reviewer. Start only after the live integration steps in NEEDS_MANAS.md have been verified.

The next Lightsail diagnostic is already queued in `jobs/next_job.json`. See [NEEDS_MANAS.md](NEEDS_MANAS.md) for the dedicated runner, report handoff, competitor evidence, Telegram and Meta setup, channel name and deployment decisions. The current server measurements and tested routes are in [RESEARCH.md](RESEARCH.md). Milestone evidence and exact resume steps are in [PROGRESS.md](PROGRESS.md) and [DECISIONS.md](DECISIONS.md). Keep downloaded media, generated media, secrets and state outside Git.

## Windows acquisition and AnalyticsDashboard

Run media acquisition only on the designated Windows PC. The PC probe writes the report used for the M0 gate. The weekly task and manual refresh share the same command. The PC needs Python 3.11+, yt-dlp, FFmpeg and Windows OpenSSH. On ordinary downloader failures the batch tries MP7, and optionally CnvMP3 and YTHD if `RESOLVE_PC_BROWSER_FALLBACK=1` and Playwright Chromium is installed. Challenges are recorded and skipped. Follow NEEDS_MANAS.md for installation, task registration and SSH setup.

Refresh discovers songs from the configured public Spotify playlist pages and phonk articles, and clips from YouTube searches and training articles. Feed defaults are in `tools/pc_sources.json`; use `--sources` for a different feed list. A refresh reads the server inventory before acquiring media when `--send` is set. It ranks by source appearances, upload date and views, then dedupes by canonical watch URL, Spotify ID and normalized recording identity where available. Spotify-matched recordings use the playlist duration. Article-only song candidates use the linked video's reported duration and carry that provenance.

The Windows batch checks five frames from the intended reel segment with local CLIP. At least three frames must meet the threshold and score highest on a training label. Accepted manifests include the per-label scores. The adjacent `.discovery.json` logs rejected clips and acquisition errors. Default limits are 10 songs, 10 clips and 3 optional speech pins per run. Override them with `RESOLVE_PC_MAX_SONGS`, `RESOLVE_PC_MAX_CLIPS`, and `RESOLVE_PC_MAX_SPEECHS`; adjust `RESOLVE_PC_VISION_THRESHOLD` from its default of 0.65.

An optional `--catalog` file pins specific YouTube watch URLs ahead of discovery. Each row takes `kind` (`song`, `clip`, or `speech`), `source`, optional `title`, and a reference `duration` for songs. Pins go through the same duplicate and validation checks.

The PC writes a ZIP with verified media and a SHA-256 manifest. SCP uploads to a temporary remote name, and SSH renames it to a ready bundle. The server operator checks the inbox while running and accepts only validated media. Set `RESOLVE_BUNDLE_INBOX` on AnalyticsDashboard and run the existing operator with `--telegram` for stale-bundle alerts. The operator generates a daily batch using the dashboard's configured volume and uses existing imported media during an acquisition outage.

## Current editorial specification

P holds one original 40–75 word paragraph in three or four sentences. L holds one original 10–25 word line. Both appear in full after a 400 ms fade. Reading time is words divided by 3.3 plus four seconds, subject to format bounds. F4 opens with an apparent paradox and reveals the resolving clause at a musical transition near the midpoint. F1 uses real speech over training footage, sentence-bounded passages and softly highlighted phrase captions. All four use sentence case and the vendored OFL Poppins SemiBold face above center.

Use reflective language about discipline, sacrifice, solitude and self-respect. Each piece earns a turn at its close. Avoid directives, gym jargon, shouting, hashtags, emojis, other creators’ captions and lyrics. Gemini supplies five scored candidates. Review can edit generated text and render a fresh preview for approval. The local fallback manuscript and a 200-reel exclusion window apply when proposals are unavailable or invalid.

Review shows how the text or transcript matched the music and training footage. New PC bundles carry CLIP mood labels plus measured frame brightness and motion. Earlier bundles remain usable. P and L favor longer-held text, and F4 enters the bandit at a low initial weight. F2 and F3 remain available for manual legacy work. Kokoro is optional.

Use `python -m tools.render_format_samples --help` for the two-per-format Review batch. Local samples stay outside the posting queue. Install Deno on the Windows acquisition PC as documented in NEEDS_MANAS.md.

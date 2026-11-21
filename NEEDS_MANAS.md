# Needs Manas

## M0 server probe

Use the **AnalyticsDashboard Lightsail** machine and run commands from its Resolve clone on main. Use a regular user with Python 3.11+, Python venv support, screen and FFmpeg (including ffprobe). The probe uses a temporary directory and caps each media transfer at 256 MiB. It may take a while and writes a checkpoint after each item. Run one probe session at a time.

On **AnalyticsDashboard Lightsail**, update the clone with existing Git credentials:

```sh
GIT_TERMINAL_PROMPT=0 git pull --ff-only origin main
```

On **AnalyticsDashboard Lightsail**, launch setup of the isolated probe environment and headless Chromium. Check the setup log before starting the probe.

```sh
screen -L -Logfile resolve-m0-setup.log -dmS resolve-m0-setup sh -c 'python3 tools/m0_probe.py --help >/dev/null && python3 -m venv .venv && .venv/bin/python -m pip --disable-pip-version-check --no-input install playwright==1.62.0 && .venv/bin/python -m playwright install chromium'
```

On **AnalyticsDashboard Lightsail**, install Playwright's Chromium system dependencies. This uses your existing non-interactive sudo access. Check the dependency log for package errors.

```sh
screen -L -Logfile resolve-m0-deps.log -dmS resolve-m0-deps sudo -n "$PWD/.venv/bin/python" -m playwright install-deps chromium
```

On **AnalyticsDashboard Lightsail**, run the Chromium preflight after both setup sessions finish:

```sh
.venv/bin/python tools/m0_probe.py --check-browser
```

On **AnalyticsDashboard Lightsail**, start the probe for 20 motivation targets and 20 separate publisher baseline items:

```sh
screen -L -Logfile resolve-m0-server.log -dmS resolve-m0-server .venv/bin/python tools/m0_probe.py --output research/m0_server_results.json
```

To test Gemini, use a key from a **billing-disabled** project. On **AnalyticsDashboard Lightsail**, with `GEMINI_API_KEY` already exported in your current shell, start this command **in place of** the previous probe command. Keep the key out of Git and command text.

```sh
screen -L -Logfile resolve-m0-server.log -dmS resolve-m0-server .venv/bin/python tools/m0_probe.py --gemini-free-project --output research/m0_server_results.json
```

Once the report has `"complete": true`, copy only `research/m0_server_results.json` from **AnalyticsDashboard Lightsail** to the same path in the Resolve clone on your **Git-enabled PC**. On that PC, from the clone root, use the line below with `RESOLVE_LIGHTSAIL_SSH` set to your Lightsail SSH alias or user@host and `RESOLVE_LIGHTSAIL_REPO` set to the absolute directory of its Resolve clone. It uses your existing SSH key.

```sh
scp -o BatchMode=yes "${RESOLVE_LIGHTSAIL_SSH:?set Lightsail SSH target}:${RESOLVE_LIGHTSAIL_REPO:?set server clone directory}/research/m0_server_results.json" research/m0_server_results.json
```

On the same **Git-enabled PC**, commit the report from the clone root on main:

```sh
GIT_TERMINAL_PROMPT=0 git pull --ff-only origin main && git add research/m0_server_results.json && GIT_TERMINAL_PROMPT=0 git commit -m "Record Resolve M0 AnalyticsDashboard results" && GIT_TERMINAL_PROMPT=0 git push origin main
```

A complete report means the matrix finished collecting results. It may contain failures, including a Chromium startup error. If it does, retain the report and send the relevant lines from the setup or probe log. The report must come from AnalyticsDashboard to count for Stage B.

## M0 follow-up diagnostic

On **AnalyticsDashboard Lightsail**, pull main and use the existing probe environment to run one audio target and one video target. Preserve the first full report.

```sh
GIT_TERMINAL_PROMPT=0 git pull --ff-only origin main && screen -L -Logfile resolve-m0-diagnostic.log -dmS resolve-m0-diagnostic .venv/bin/python tools/m0_probe.py --limit 1 --output research/m0_server_diagnostic.json
```

Commit the separate diagnostic JSON to main after it reports completion. Keep the full probe report for comparison.

## M0 competitor evidence

The available public search and profile snapshots leave the leading faceless pages' Reel duration, cadence and plays unverified. If those metrics are visible in your Instagram session, share a screenshot or screen recording of the Reels grids and the latest three Reel links for pages you recognize as direct competitors. Include dates, visible plays and duration where available. No login credentials are needed. This can be done independently of the Lightsail probe.

## Later actions

- **M8 Telegram:** Create a separate bot in BotFather and provide its token and approved chat ID through the server's ignored environment file. Never commit them.
- **M9 Meta:** Create a new Meta app for Resolve and connect the intended Instagram professional account and Facebook Page. Do this separately from the other projects. Exact permission and dry-run steps belong to M9.
- **Channel name:** Confirm the public name before go-live. Resolve is the repository name.
- **Deployment:** Choose packaging and resource limits before M13. Keep both options open until then.

## Server job runner and offline alternatives

Use a dedicated non-root queue clone on main and a separate, locally reviewed probe checkout with a working .venv and sandboxed Chromium. The queue can select probe options only. Updating executable probe code requires updating that separate checkout. Store a fine-grained GitHub token scoped to Resolve with Contents read/write in a file outside both checkouts, mode 600. Configure the queue clone Git identity before starting.

On **AnalyticsDashboard Lightsail**, from the queue clone, set RESOLVE_PROBE_CHECKOUT and RESOLVE_TOKEN_FILE to the absolute paths, then start:

```sh
screen -L -Logfile resolve-runner.log -dmS resolve-runner bash tools/server_runner.sh "$PWD" "${RESOLVE_PROBE_CHECKOUT:?}" "${RESOLVE_TOKEN_FILE:?}"
```

A job at jobs/next_job.json takes id (unique), limit (1–10 targets per kind), and no_browser (boolean). Reports appear under research/server_jobs. Review a new probe revision locally before updating the separate checkout.

If providers still fail, a weekly refresh on an existing home or office machine avoids paid services but depends on its uptime and requires syncing media. An existing permitted residential connection could relay traffic, but it adds operational work and any provider blocks still apply. A direct-download library or manual creator supply is free where offered and simpler to validate, but changes the available track and clip catalog. A paid residential proxy falls outside the current no-paid-services rule. No option is selected.

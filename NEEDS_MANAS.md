# Needs Manas

## M0 server probe

Use the **AnalyticsDashboard Lightsail** machine and run commands from its Resolve clone on main. Use a regular user with Python 3.11+, Python venv support, screen and FFmpeg (including ffprobe). The probe uses a temporary directory and caps each media transfer at 256 MiB. It may take a while and writes a checkpoint after each item. Run one probe session at a time.

On **AnalyticsDashboard Lightsail**, update the clone with existing Git credentials:

```sh
GIT_TERMINAL_PROMPT=0 git pull --ff-only origin main
```

On **AnalyticsDashboard Lightsail**, launch setup of the isolated probe environment and headless Chromium. Check the setup log before starting the probe.

```sh
screen -L -Logfile resolve-m0-setup.log -dmS resolve-m0-setup sh -c 'python3 -m venv .venv && .venv/bin/python -m pip --disable-pip-version-check --no-input install playwright==1.62.0 && .venv/bin/python -m playwright install chromium'
```

On **AnalyticsDashboard Lightsail**, start the 20-item probe:

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

## M0 competitor evidence

The available public search and profile snapshots leave the leading faceless pages' Reel duration, cadence and plays unverified. If those metrics are visible in your Instagram session, share a screenshot or screen recording of the Reels grids and the latest three Reel links for pages you recognize as direct competitors. Include dates, visible plays and duration where available. No login credentials are needed. This can be done independently of the Lightsail probe.

## Later actions

- **M8 Telegram:** Create a separate bot in BotFather and provide its token and approved chat ID through the server's ignored environment file. Never commit them.
- **M9 Meta:** Create a new Meta app for Resolve and connect the intended Instagram professional account and Facebook Page. Do this separately from the other projects. Exact permission and dry-run steps belong to M9.
- **Channel name:** Confirm the public name before go-live. Resolve is the repository name.
- **Deployment:** Choose packaging and resource limits before M13. Keep both options open until then.

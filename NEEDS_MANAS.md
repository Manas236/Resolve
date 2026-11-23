# Needs Manas

## M0 one-pass audio and video probe

On the **Windows acquisition PC**, run from the Resolve clone in PowerShell on the residential connection. Use Python 3.11+, FFmpeg and ffprobe, Git, Windows OpenSSH Client, and Deno 2.3 or newer. Install these with your existing local tooling and make them available on PATH.

```powershell
winget install --id DenoLand.Deno -e --accept-source-agreements --accept-package-agreements --disable-interactivity
$env:GIT_TERMINAL_PROMPT='0'; git pull --ff-only origin main
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip --disable-pip-version-check --no-input install -r requirements-pc.txt
Start-Process -FilePath .venv\Scripts\python.exe -ArgumentList '-m tools.pc_acquisition probe --output research/m0_pc_results.json' -RedirectStandardError resolve-pc-probe.log -Wait
```

Open a new PowerShell window after installing Deno and verify `deno --version` works before the probe. The scheduled task needs the same account PATH.

For optional browser fallbacks on the **Windows acquisition PC**, install Playwright with `.venv\Scripts\python.exe -m pip --no-input install playwright`, install Chromium with `.venv\Scripts\python.exe -m playwright install chromium`, and set `RESOLVE_PC_BROWSER_FALLBACK=1` in .env.

The Windows probe is a local process. The Linux `screen` wrapper applies to long-running Lightsail commands. Verify the JSON says `"complete": true`, then commit and push only the JSON from the **Windows acquisition PC**.

```powershell
$env:GIT_TERMINAL_PROMPT='0'; git add -- research/m0_pc_results.json; if ($LASTEXITCODE -eq 0) { git commit -m 'Record Windows residential acquisition probe' }; if ($LASTEXITCODE -eq 0) { git push origin main }
```

## M5 Windows weekly refresh and SSH enrollment

On **AnalyticsDashboard Lightsail**, run as the existing deployment user. In the server .env set `RESOLVE_BUNDLE_INBOX` to the absolute path printed by this command.

```sh
install -d -m 700 "$PWD/state/incoming" && realpath "$PWD/state/incoming"
```

On the **Windows acquisition PC**, set local .env `RESOLVE_SSH_HOST` to the existing SSH login and hostname, `RESOLVE_SSH_KEY` to the existing Lightsail key path, and `RESOLVE_SSH_INBOX` to the same absolute path. Use the existing verified host key entry in the Windows OpenSSH known-hosts file. Keep the key and .env out of git.

On **AnalyticsDashboard Lightsail**, start the updated operator using the same state directory as the dashboard and `RESOLVE_BUNDLE_INBOX` from above. It writes `inventory.json` to the inbox even when the library is empty. The Windows refresh reads that inventory over SSH before acquiring media. Set `GEMINI_API_KEY` from the designated unbilled project and configure the optional speech models as documented in README. Telegram credentials have not been created. The operator can run without Telegram until you set up the bot under M8.

On the **Windows acquisition PC**, run the first refresh and register the Sunday task. The first refresh with a candidate clip downloads the free CLIP weights (about 605 MB), then scores frames locally on CPU. The task runs under your signed-in Windows account and starts when available after a missed trigger. Your editorial review happens on finished reels.

```powershell
Start-Process -FilePath .venv\Scripts\python.exe -ArgumentList '-m tools.pc_acquisition refresh --output state/pc_bundle.zip --send' -RedirectStandardOutput resolve-pc-refresh.log -RedirectStandardError resolve-pc-refresh-errors.log -Wait
powershell.exe -NoProfile -NonInteractive -File tools\register_pc_refresh.ps1
```

## M0 competitor evidence

The available public search and profile snapshots leave the leading faceless pages' Reel duration, cadence and plays unverified. If those metrics are visible in your Instagram session, share a screenshot or screen recording of the Reels grids and the latest three Reel links for pages you recognize as direct competitors. Include dates, visible plays and duration where available. No login credentials are needed. This can be done independently of the Lightsail probe.

## Later actions

- **M8 Telegram:** Create a separate bot in BotFather and set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and TELEGRAM_REVIEWER_ID in the server's ignored environment file. Never commit them.
- **M9 Meta:** Create a new Meta app for Resolve and connect the intended Instagram professional account and Facebook Page. Do this separately from the other projects. Exact permission and dry-run steps belong to M9.
- **Channel name:** Confirm the public name before go-live. Resolve is the repository name.
- **Deployment:** Choose packaging and resource limits before M13. Keep both options open until then.

## Retire the old server probe runner

If the old diagnostic loop is still active on **AnalyticsDashboard Lightsail**, stop it from the deployment account. The completed report remains in `research/server_jobs/`. Remove the runner-only fine-grained token from GitHub if it has no other use.

```sh
screen -S resolve-runner -X quit
```

## M9 live posting setup

In Meta for Developers, create a dedicated Resolve app and add the Instagram API with Facebook Login. Connect the target Instagram professional account to the Facebook Page and grant the Resolve app access as a tester or developer during development. Configure Instagram content publishing and Page publishing permissions in the app and obtain a token for the connected assets. In the ignored AnalyticsDashboard .env, supply META_ACCESS_TOKEN, META_IG_USER_ID, META_PAGE_ID and the app's supported META_GRAPH_VERSION. Keep META_DRY_RUN=true during setup. Live upload needs a public HTTPS URL for each reel that Meta can fetch. The serving method and a first explicitly approved live test remain pending.

## M13 AnalyticsDashboard profile and health check

After reviewing the deployment draft, run these from the Resolve probe checkout on **AnalyticsDashboard Lightsail** as ubuntu. The install checks the profile syntax and stops if a different local profile already exists. In that case keep your existing profile, compare it with the documented attachment and verify it below.

```sh
sudo -n bash tools/deploy_analyticsdashboard.sh install-profile
```

```sh
bash tools/deploy_analyticsdashboard.sh verify
```

Verify reports the loaded AppArmor profile, sandboxed Chromium launch and verified provider counts independently. A registry failure is expected while the committed server report still shows zero verified target providers.

## M12 interactive dashboard acceptance

On **AnalyticsDashboard Lightsail**, from the reviewed Resolve checkout after the Chromium preflight passes, run the browser acceptance test in its own screen session. It uses disposable local fixtures and sends no live Telegram or Meta requests.

```sh
screen -L -Logfile resolve-dashboard-acceptance.log -dmS resolve-dashboard-acceptance env RESOLVE_BROWSER_E2E=1 .venv/bin/python -m unittest discover -s tests -p test_dashboard_browser.py -v
```

The test writes all eight pages in both themes to `research/ui_screenshots/` on AnalyticsDashboard. Copy that directory for review and retain the unittest log. The browser remains sandboxed.

## M6 and M7 editorial review

The sample links are in PROGRESS.md. The legacy voice samples are optional. For the publisher reels, review the six speech examples for actual speaking cadence, word highlighting and sentence-complete endings. The existing checks establish timing and streams. They leave editorial judgement open.

## Office PC speech preview

Kokoro setup and voice selection are optional legacy F3 tasks. For two current F1 previews use the office PC's actual training footage, song, speech and local Whisper model. From the Resolve checkout on the **Windows office PC**, set the three local media paths in the environment and run the following PowerShell line. The command reads the Whisper model path and optional Gemini key from your existing .env.

```powershell
.venv\Scripts\python.exe -m tools.render_format_samples --state state/dashboard --video "$env:RESOLVE_SAMPLE_VIDEO" --song "$env:RESOLVE_SAMPLE_SONG" --speech "$env:RESOLVE_SAMPLE_SPEECH" --formats F1
```

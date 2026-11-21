# Needs Manas

- **M0 server probe:** Pending completion of the Stage A probe and its corpus. Exact AnalyticsDashboard screen and report handoff commands will be added here with that unit.
- **M0 optional Gemini:** Use only a billing-disabled Gemini project. A live free-tier grounding result is pending. The probe will accept a key via environment configuration and must never write it to the report.
- **M8 Telegram:** Create a separate bot in BotFather and provide its token and approved chat ID through the server's ignored environment file. Never commit them.
- **M9 Meta:** Create a new Meta app for Resolve and connect the intended Instagram professional account and Facebook Page. Do this separately from the other projects. Exact permission and dry-run steps belong to M9.
- **Channel name:** Confirm the public name before go-live. Resolve is the repository name.
- **Deployment:** Choose packaging and resource limits before M13. Keep both options open until then.

### Optional early smoke check

The partial probe has a direct-media and public watch-page smoke case only. The full Lightsail gate command is pending Stage A. If running the smoke check, use a current clone on **AnalyticsDashboard Lightsail**, from its repo root with Python 3.11+, screen and FFmpeg (including ffprobe) present.

```sh
screen -dmS resolve-m0-smoke python3 tools/m0_probe.py --output research/m0_lightsail_smoke.json
```

After the report appears, copy only the JSON to your Git-enabled **PC**, into the same path in its Resolve clone, then run this single line from that clone. This early report has smoke scope. Keep it separate from m0_server_results.json.

```sh
GIT_TERMINAL_PROMPT=0 git add research/m0_lightsail_smoke.json && GIT_TERMINAL_PROMPT=0 git commit -m "Record Resolve Lightsail smoke results" && GIT_TERMINAL_PROMPT=0 git push origin main
```

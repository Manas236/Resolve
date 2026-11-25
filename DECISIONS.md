# Resolve decisions

## Published quotations, 25 November 2026

Manas requires published, attributable wording for P, L and F4. Gemini may classify mood. The generated manuscript and model writer are archival. Store the speaker, exact wording, work and source URL. Exclude disputed, misattributed and unsourced material. Prefer observed Goodreads likes and independently verified coverage, then theme fit. Preserve unknown popularity as unknown. Use a 200-reel quote window and five-reel speaker window. Show the speaker below the quote and reserve the largest type for one-liners.


- Repo is the sole build handoff. No reuse of other project code. Push completed, tested units to main.
- Keep working in the same turn across completed units. Test, update PROGRESS.md, commit, push and begin the next unblocked unit. End only at the usage limit, when all remaining work is blocked on NEEDS_MANAS.md, or when the build is done.
- On 24 November 2026 the hourly **Continue the Resolve build** follow-up was enabled in the current build conversation. First scheduled follow-up is 10:37 IST. The previous conversation’s schedule is paused. Prompt: “Continue the Resolve build. Read PROGRESS.md, pick the first unblocked open item and keep working. Do not end your turn while unblocked work exists and usage remains. No partial-status replies.” Manage it in ChatGPT Scheduled.
- Default audience: English-speaking recreational lifters building consistent strength-training habits. Training footage, specific gym hooks and actionable short cues fit a faceless format. Competitor sampling in M0 must validate reach and format assumptions. Keep the default configurable.
- Keep deployment packaging and resource limits undecided. Design for headless Linux and make configuration transport-independent.
- Start with Python 3.11+ and standard-library runtime configuration and tests. This keeps the first scaffold runnable on headless Ubuntu without package or deployment choices.

## Fixed build contract

English, international, faceless. Free/open-source tooling and no paid services. AnalyticsDashboard AWS Lightsail is ground truth for network tests. Never commit secrets, media or caches. Commit .env.example only. No yt-dlp. Download from public direct-download websites and public online conversion websites, using plain HTTP requests where allowed and standard headless Chrome with Selenium or Playwright where necessary. Never solve or bypass bot checks. Treat downloads as untrusted, isolate them, accept only audio/video, verify with ffprobe and never execute downloaded content. Keep a configurable registry with 10+ candidates per kind and automatic rotation/health ranking. Add a provider with an entry and a small adapter.

Source tracks from public Spotify playlist pages (no Spotify API) and public articles, using HTTP or a browser as needed. Rank deduped tracks by recency and frequency. Source trending speeches, movie scenes, sports and podcasts for video. For both libraries, try YouTube Most replayed, then public Instagram/TikTok sound or similar pages, then Gemini only with verified free Search grounding, then audio chorus/energy detection. Reject social/Gemini timestamps that do not match a chorus, drop or audio energy peak. Record timestamp method. Reject wrong song versions beyond a 3-second duration difference. Refresh both libraries weekly by default, configurable to monthly. Track performance and mute history per asset and feed into ranking.

F1 mixes intact speech and ducked viral music with word-highlight captions. F2 uses footage, on-screen quote and music without voice. F3 uses footage, TTS and music with word-highlight captions. F4 runs alongside these only if M0 supports it. Use real word timestamps from the clean speech, large heavy hook text, and sentence-complete endings. Standard reels 1080×1920, 9:16, 20–25 seconds. Go longer only when both media segments justify it. Bake audio into each reel. User accepts platform muting, takedowns and strikes.

Two reels per day, configurable slots and format weights. Telegram Approve, Reject, Regenerate and Muted. Approval-only week one, explicit auto switch thereafter. Never post without Telegram approval while in approval mode. Instagram and Facebook via a separate new Meta app and dry-run mode. YouTube stays an adapter slot. Infer possible mute from view collapse relative to format baseline, and keep the manual mute signal distinct. Dashboard must work with runner on and off. See milestone criteria for its controls.

For user-only work, document the exact machine and single-line non-interactive commands in NEEDS_MANAS.md. Wrap long-running commands in screen. Provide placeholders for secrets and continue unrelated work.
- Treat smoke results as their own report scope. Record sizes, hashes and media metadata while deleting temporary downloads, so proof can travel in Git without media. Use three-second RMS windows only as a baseline for the energy fallback.

## M0 measurement decisions

- Use a fixed publisher-backed transport benchmark so version drift and changed durations can be measured. Keep publisher direct controls separate from independent converter counts and treat blocked/skipped rows as zero successes against the full denominator.
- Keep the probe encapsulated in one script with a checked-in corpus. Use Playwright only for public user-facing forms and explicit public timestamp links. Capture status and bounded FFmpeg metadata without browser profiles, response bodies, generated URLs or downloaded files in the report.

- Stage B's gate counts only the public motivation corpus. Keep publisher tracks and open films as a separately reported control group.
- Before Stage B, build site-agnostic M2 registry, adapter contract, rotation and media validation. Keep M2 unchecked and site availability unknown until real evidence supports promotion.

## AnalyticsDashboard Chromium sandbox

- Manas confirmed Chromium preflight passed on Ubuntu 24 after an AppArmor fix for `No usable sandbox`. Unprivileged user namespace restrictions caused the failure. The host uses `/etc/apparmor.d/playwright-chromium` with ABI 4.0, attachment `/home/ubuntu/.cache/ms-playwright/chromium*/**/chrome*`, `flags=(unconfined)`, and a `userns,` rule, loaded with `apparmor_parser -r`.
- M13 deployment must install or verify that narrowly scoped profile and reload it when changed. The health check must verify the profile is loaded and perform a Chromium launch as the runtime user with sandboxing enabled. Never fall back to `--no-sandbox`.
- While the server probe runs, site-independent M2 and M3–M7 groundwork is authorized. Continue to require the applicable live evidence for milestone completion.

## Speech synthesis

- Prefer Piper with the local Lessac model provisionally. In the three-engine comparison, Whisper recovered all scripted words from both Piper and Flite, but Piper preserved more sentence breaks. eSpeak NG changed one word in Whisper's output. This is a cadence and intelligibility proxy. The blind listening comparison and Lightsail runtime remain open.

## Performance measurement

- Compare 24-hour view snapshots. Infer a possible mute only below 20% of the same-format median with at least five other measured, unreported peers and a median of at least 100 views. Store Telegram mute reports as a distinct signal. Use the combined signal as a smoothed asset penalty and a loss for the format selector.

## 22 November 2026 architecture change

- Manas moved all media acquisition to a designated Windows PC on a residential connection after diagnostic 04 verified zero on all nine Lightsail routes. The updated M0 gate uses the residential PC report. Windows runs yt-dlp as primary and converters on ordinary failure. Challenges halt probing that source. AnalyticsDashboard only imports media from the PC and runs render, review, posting and measurement.
- Weekly Windows Task Scheduler and a manual CLI share the refresh command. Uploads use the existing Lightsail key and require a verified SSH host key. A unique ready filename, a SHA-256 manifest and server-side ffprobe validation protect import. Send a Telegram alert after nine days without a new verified bundle and retain the existing library.
- Training footage is required for production video. F1 uses separately acquired speech audio from speeches, film or podcasts. Gym phonk and gym edits lead song selection. The dashboard marks placeholder/test reels and publishing requires verified production provenance.
- Kokoro-82M and Piper medium form the new audition set. Manas chooses the production voice. Engine, local model paths and the voice identifier are deployment configuration.

- Windows weekly acquisition now builds its own catalog. Public feed inputs and batch limits are configurable, existing library identities come from a server-side inventory, and a local CLIP majority vote screens candidate footage. The editorial decision remains approval of the finished reel. A catalog is optional for prioritized pins.

- Generated on-screen text is separate from the social caption. Gemini 2.5 Flash is preferred, and 512 validated lines cover quota gaps without repeating in a 100-reel window. An edit creates a new preview ID so a stale Telegram approval cannot approve revised text.
- Default caption treatment is Anton. Bebas Neue and Montserrat Black are selectable. FFmpeg burns the vendored OFL fonts, and librosa aligns F2 transitions within a bounded window near the intended pacing.

- F1 uses a separate audio source, full-source transcription, and sentence-bounded 20–35 second passage selection. Mood compatibility and audio energy rank songs. F4 enters the rotation as an experiment with a conservative prior. F3 defaults to Kokoro am_michael for a deeper male read.

## Poetic editorial direction, 24 November 2026

Manas retired F2 and F3 from Auto. Both retain weight zero and manual legacy tooling. Kokoro is optional. P holds one reflective paragraph, L one line, and F1 carries real speech over training footage. F4 tests a two-part paradox: the first observation invites a reading that the second quietly overturns at a musical transition. This isolates the effect of delayed resolution against L's immediate reading. Start F4 at a low weight. Use original sentence-case writing, quiet captions, text-led music and footage selection, and retain the selection reasoning in Review.

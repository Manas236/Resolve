# Resolve progress

Session 5 checkpoint. Stage B evidence is on main and the M0 gate failed. See RESEARCH.md for measured results. Continue site-independent groundwork pending a successful re-probe and competitor evidence.

## Milestones

- [ ] **M0 Research gate.** Stage A: identify candidate sites and methods, run local first tests, and create a self-contained `tools/m0_probe.py` that produces `research/m0_server_results.json`. Document the non-interactive Lightsail screen command and how to commit results. Stage B: analyse committed server evidence in RESEARCH.md.
  - [ ] Three or more working song download sites with measured success rates over 10 songs each.
  - [ ] Three or more working video download sites with measured success rates over 10 videos each, counting only verified 720p or better.
  - [ ] On 10 songs and 10 videos, report all four timestamp methods and which worked. Verify Gemini free-tier Search grounding and cross-check social/Gemini timestamps against audio.
  - [ ] Compare plain HTTP and headless Chrome for applicable steps and report headless/browser/IP blocks with evidence. Skip CAPTCHA and bot checks.
  - [ ] Study 10 leading faceless competitors in the chosen niche. Record formats, hooks, duration, cadence and observed performance. Propose F4 only if supported by observations.
  - For failed download or timestamp gates, add candidates or methods and re-probe. M1 and the site-agnostic M2 foundation are allowed before the gate passes.
- [x] **M1 Scaffold.** Fresh repo, runtime config and .env.example, logging, folder layout, automated tests and README. Exercise normal startup and invalid config, local runtime directory creation and repeat startup.
- [ ] **M2 Downloader.** Configurable registry with 10+ candidates each for audio and video and health scores. Verify adapters, automatic rotation, health checks, retries, cache, isolated audio/video-only ffprobe validation and song duration matching within 3 seconds.
- [ ] **M3 Song library.** Source from public Spotify playlist pages and articles, dedupe and rank by frequency and recency, select and record cross-checked timestamps, download correct versions and cut. Prove 30+ songs with verified segments.
- [ ] **M4 Video library.** Source, rank, select and record cross-checked timestamps, download and cut trending speech, movie, sports and podcast clips. Prove 30+ clips with verified segments.
- [ ] **M5 Refresh.** Run both library refresh jobs weekly by default or monthly by configuration, add new candidates, retire stale entries, and run and verify the site health job and ordering.
- [ ] **M6 Captions and TTS.** Verify real word timestamps on clean speech, benchmark 3+ free engines on the same script, retain samples outside git, record the naturalness decision and integrate the selected engine.
- [ ] **M7 Render.** Produce three finished 1080×1920 reels each for F1, F2, F3 and research-approved F4, if any. Log paths and check each for sentence completion and caption sync where applicable. Standard duration is 20–25s, with longer outputs only when both segments justify it.
- [ ] **M8 Telegram.** Verify preview and Approve, Reject, Regenerate and Muted actions with real Telegram evidence. Verify duplicate and unauthorised actions do not change state.
- [ ] **M9 Posting.** Implement a dry-run and a new Meta app adapter for Instagram and Facebook, with a clean future YouTube adapter slot. Verify platform integration within explicit posting approval.
- [ ] **M10 Runner.** Prove two reels/day by default, configurable slots, week-one approval mode, explicit auto mode, format A/B and performance-based choice, and restart recovery without duplicate posts.
- [ ] **M11 Performance.** Track views and observed/inferred mute signals by reel, song, clip and format. Prove ranking feedback and record the baseline used for inferred mutes.
- [ ] **M12 Dashboard.** Password login and automated end-to-end proof for every control with runner on and off. Exercise make N (auto or selected format), URL creation, approve/reject/regenerate/delete, queue reorder, caption/hashtag edits, library preview/pin/retire/timestamp rerun, registry health/enable/disable/retest, settings, runner start/stop, logs/failures and performance by reel/song/clip/format. Routine review actions update without page reloads.
- [ ] **M13 Hardening.** Prove retries, Telegram failure alerts, one-command health check including registry, full library-refresh-to-dry-run end-to-end test and AnalyticsDashboard deployment script after deployment choices are made.
- [ ] **M14 Handoff.** Record working features with proof, pending user actions, known weaknesses and exact go-live steps here.

## Completion protocol

On every resume read the four root handoff files first. Continue the first unchecked, unblocked item. After each finished unit, run tests, record evidence here, commit and push to main. Before any tick, run the full suite and audit the unit against every criterion, including applicable bad inputs, dead sites, empty results and restarts. Fix failures and record the audit below. Mocks do not count as proof of real downloads, integrations, renders or server behavior. Near a usage limit, checkpoint and put the exact next action at the top.

## M1 audit

The full suite passed with six tests via `python3 -m unittest discover -s tests -v`. It exercised normal startup, restart with existing state, environment precedence, malformed and duplicate configuration, invalid log level and unusable state path. Compileall and staged whitespace checks passed. Git ignore matched the local .env, a sample media path and Python cache.

## M0 Stage A checkpoint — partial

The self-contained probe implements bounded, isolated direct downloads, ffprobe container/stream and resolution validation, an audio-energy smoke test, and public watch-HTML replay detection. It marks the output as smoke scope. The full suite has 11 passing tests, including duration tolerance, low resolution, nonmedia stream rejection, empty and silent audio, absent heatmap and visible challenge text. The code still needs the matched test corpus, provider adapters, headless and social timestamp comparisons, a live Gemini check when eligible, and the complete competitor study. The local Chromium launch failed before navigation because socket creation was denied. This is an executor limitation, not evidence of a remote browser or IP block.

Real local probe evidence in `research/m0_local_smoke.json`: HTTP downloaded and ffprobe verified one 372.715-second MP3 (8,945,229 bytes) and one 1920×1080 MP4 (11,916,526 bytes). FFmpeg decoded both and found RMS peaks at 351s and 9s respectively. SampleLib audio returned 301 and was recorded as a failed attempt. The YouTube watch page returned 200 with no parseable replay markers. This run establishes local smoke behavior. The server gate remains open.

## M0 Stage A matrix checkpoint

The probe now schedules 10 publisher-referenced epic tracks and 10 Blender open films with matching watch IDs. It records direct controls, MP7/noTube/DirectYT browser candidate outcomes, public watch HTML and headless replay observations, optional billing-disabled Gemini grounding and grounded timestamp requests, social links mapped to the exact upload where available, and audio-energy checks. Social coverage is partial because the benchmark currently has no verified per-item social links. The browser recipes require live server confirmation. Reports checkpoint after each item and include explicit skips, verified counts and fixed denominators.

13 automated tests passed. In the two-item diagnostic (`research/m0_local_matrix_sample.json`), one in-range publisher MP3 and one 1280×720 publisher MP4 passed real download and media validation. Plain HTTP returned 200 for the MP7 landing and 403 for the noTube and DirectYT landings. Chromium failed to start in this workspace, so the diagnostic proves no converter results. A 20-item HTTP-only diagnostic is in progress. Stage B and the 10-account study remain unchecked.

The browser candidate audit checked the current public page markup. The noTube page uses numbered first-party service origins, so the browser allowlist now permits those exact HTTPS origins and rejects deceptive lookalikes. Reports include the Git revision and FFmpeg/ffprobe versions so server results can be tied to the probe revision. The full suite passed 14 tests.

The full local HTTP diagnostic finished all 20 rows (`research/m0_local_http_matrix.json`). The publisher controls verified 10/10 audio files within duration tolerance and 7/10 videos at 720p or better. Three video downloads timed out. Audio energy selected offsets for all 17 verified files. Sixteen watch pages yielded no replay markers and four returned HTTP errors. Headless conversion was excluded from this run, so these counts are publisher and timestamp evidence only. Full suite passed 15 tests. The current probe adds outcome summaries and uses a verified converter's audio when the publisher control fails. The documented commands use a full run with browser enabled.

A bounded repeat of the three timed-out publisher controls verified Agent 327 and Spring at 720p or better. Caminandes: Llamigos timed out again. The first-pass rate remains 7/10, and nine distinct video controls verified across the two runs. The current schema's two-item diagnostic completed successfully, recorded its code/tool versions, and reported a Big Buck Bunny transfer timeout where the earlier run had succeeded. These are local transport observations. Await Lightsail for converter and network conclusions.

## M0 target corpus revision

The target matrix now uses 10 gym-edit tracks from public Spotify workout playlists and 10 motivational movie, podcast and speech uploads. It measures 8 dual-format converter candidates and 2 additional direct MP3 leads. Two direct pages have matched tracks, and remaining direct rows explicitly record missing matches. The prior 20 publisher items run separately and contribute zero to converter gate counts. Python 3.10 fails before probe startup. Chromium preflight exited clearly on this workspace's launcher restriction. The full suite passed 17 tests including the fixed target denominator and baseline separation. Provider availability and browser conversion remain pending real Lightsail measurements.

## M2 site-agnostic foundation

The runtime now has a JSON provider registry, stream adapter interface, health-weighted rotation, bounded staging and ffprobe checks for actual container, streams, duration tolerance and video resolution. Fixture tests cover disabled and unavailable adapters, fallback ordering, duplicate names, cleanup on failure and the size limit. Real FFmpeg/ffprobe tests cover exact three-second tolerance, wrong duration, HTML rejection and 360p versus 720p. M2 is unchecked. Production site adapters, live evidence, persistent health, cache and scheduled health checks remain for Stage B and later work.

The revised two-row HTTP-only diagnostic completed against LOS VOLTAJE and the Rocky Balboa speech. Both watch pages returned HTTP 200 without parseable replay markers. Three old landing URLs returned 200, six returned 403/503, and Loader redirected to a canonical first-party page. The probe now uses that canonical URL. This was a local diagnostic with Chrome disabled, so it supplies no converter or Lightsail success evidence. The full suite passed 22 tests after the M2 foundation and the final probe preflight changes.

## Provider-independent clip groundwork

Added a clip-window planner that keeps a candidate timestamp in bounds, preserves lead-in where possible, and handles short source media. Added tests for both edges, short media and invalid timestamps. All 25 tests passed. Recorded the successful AnalyticsDashboard AppArmor setup and the M13 deployment and health-check requirements in DECISIONS.md.

## M0 Stage B first report

AnalyticsDashboard's completed report measured zero verified downloads and zero selected timestamps across the 20 motivation targets. Chromium launched and the separate controls verified 10 audio and nine video assets. M0 and M2 remain unchecked. See RESEARCH.md for the per-provider breakdown and re-probe priorities.

The probe now distinguishes browser landing, form interaction, result-link lookup and media transfer failures. Result-link diagnostics include fetch/XHR status codes without request URLs or response content. The local suite passed 25 tests. Live browser outcomes still require a diagnostic run on AnalyticsDashboard.

## M6 caption groundwork

Added clip-relative SRT export from timestamped transcription cues. It clips cue timing at both boundaries and rebases to the exported clip. All 27 local tests passed. This is subtitle groundwork. Real transcription and word highlighting remain unverified. The separate Lightsail diagnostic had not arrived on main.

## M7 vertical framing groundwork

Added an FFmpeg center-crop filter that accounts for source display aspect ratio. Real local FFmpeg runs produced 1080×1920 square-pixel output for standard landscape and anamorphic fixtures. All 28 tests passed. The full renderer and editorial checks remain open.

## Server runner and replay parsing groundwork

Added an opt-in server runner and handoff notes, including no-paid fallback tradeoffs. The queue can select only a bounded probe job. Executable probe code comes from a separate locally reviewed checkout. The replay parser now decodes complete heat-marker objects instead of dropping objects containing nested metadata. Live replay availability and server runner execution remain unverified.

Queued the two-target Chromium diagnostic as `m0-browser-diagnostic-01`. It can run once the AnalyticsDashboard runner is installed and points to a reviewed checkout that includes the updated probe. Its separate report will appear at `research/server_jobs/m0-browser-diagnostic-01.json`. Added a regression test for nested replay marker metadata. All 29 local tests passed. No live execution or download success has been verified.

## Provider and local pipeline follow-up

MP7 now follows its source-observed asynchronous JSON flow. CnvMP3 and YTHD use targeted public controls. Loader is skipped because the current script advertises a proof-of-work challenge. Recorded HTTP checks for additional audio/video leads in RESEARCH.md. HTTP listing responses do not qualify a route for production.

Added local vertical MP4 rendering, Telegram reviewer/chat callback validation, and a read-only loopback dashboard for probe reports. A first TTS benchmark generated local WAVs with three Flite voices on the same script. This is one engine and does not satisfy the three-engine or listening requirements. All 30 tests passed. Live provider tests, Telegram transport, and authenticated dashboard controls remain open.
A two-second render from the SampleLib publisher control produced H.264 at 1080×1920 and AAC audio, verified with ffprobe.

## Dashboard controls groundwork

The loopback dashboard now renders batches from configured local media, persists approval decisions in SQLite, and starts or stops a worker that processes queued renders. Stop lets the active render finish. These controls use the local renderer. Live sourcing, posting, password login and full dashboard acceptance remain open.

## A. Four-target server diagnostic

Queued `m0-browser-diagnostic-02` with browser enabled and two targets per media kind. A fresh ID prevents the previous one-per-kind report from suppressing execution. Use a reviewed checkout containing the rewritten adapters and replay parser. The probe runs the existing provider matrix, including MP7, CnvMP3 and YTHD. Server execution remains pending.

## B. Dashboard authentication and review actions

The loopback CLI now requires a salted scrypt password hash, issues an expiring HttpOnly and SameSite session cookie, supports logout, and locks login for 60 seconds after five failures. The review queue supports regeneration as a separate candidate and soft deletion. Tests exercise lockout, expiry, logout, real regenerated output state and invalid review transitions. The remaining dashboard controls and complete milestone test are open.

Dashboard URL submission now calls the downloader interface and returns an explicit error with the default empty provider registry. Caption and hashtag edits persist in SQLite. The posting queue accepts an exact permutation of approved reel IDs. The HTTP test covers these controls with the worker stopped and running, including invalid URLs and duplicate order IDs. Library, registry, settings, logs, performance and no-reload updates remain open.

Dashboard settings now persist validated posting slots, daily volume, approval mode, refresh cadence and F1/F2/F3 weights. Routine forms submit with fetch and replace the pipeline section in place, while login/logout use normal navigation. The full 32-test suite passed, including persisted settings across a fresh Pipeline instance and rejection without mutation. Scheduler consumption and interactive browser verification remain open.

Dashboard clip and song libraries now support file preview, pin, retire and real FFmpeg energy timestamp detection. Clip pin and retirement affect batch source selection. Local song entries can be loaded with `--song`. The HTTP integration test checks actual WAV bytes and energy detection with the runner off, rejects a clip with no audio, and verifies retirement and blocked re-pinning with the runner on. All 32 tests passed. Registry controls, logs and performance are still open.

"""Server-rendered Resolve dashboard pages."""
from html import escape
from pathlib import Path


PAGES = ("Review", "Make", "Queue", "Libraries", "Sources", "Settings", "Performance", "Logs")


def document(body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Resolve</title><script src="/static/dashboard.js" defer></script>'
            '<link rel="stylesheet" href="/static/dashboard.css"></head><body>'
            + body + '</body></html>')


def layout(page, status, content, token, logout):
    nav = "".join(f'<a href="/{name.lower()}"'
                  f'{""" aria-current="page" """ if name == page else ""}>{name}</a>' for name in PAGES)
    return document(
        '<aside class="sidebar"><a class="brand" href="/review"><span>R</span> Resolve</a>'
        f'<nav aria-label="Dashboard">{nav}</nav><footer><button type="button" id="theme">Switch theme</button>'
        f'{logout}</footer></aside><div class="workspace">{status}<main id="pipeline" '
        f'data-page="{page.lower()}" data-token="{token}"><h1>{page}</h1>{content}</main></div>'
        '<p id="feedback" role="status" aria-live="polite"></p>'
    )


def status_bar(pipeline, form, zone):
    settings = pipeline.settings()
    slots = settings["posting_slots"].split(",")[:int(settings["volume"])]
    running = pipeline.running
    count = sum(state == "review" for _, state, _ in pipeline.list_reels())
    return (f'<header id="status-bar"><span class="state {"online" if running else ""}">'
            f'<i></i>Runner {"running" if running else "stopped"}</span>'
            f'<span>Today <strong>{escape(" · ".join(slots))}</strong> {escape(zone)}</span>'
            f'<span><strong>{count}</strong> pending approval</span>'
            + (form("stop", "", "Stop runner") if running else form("start", "", "Start runner")) + '</header>')


def empty(message):
    return f'<p class="empty">{message}</p>'


def asset_field(asset_id):
    return f'<input type="hidden" name="id" value="{asset_id}">'


def review(pipeline, form):
    editorial = pipeline.editorial()
    cards = []
    for reel_id, state, error, source, format_id, song in pipeline.reel_details():
        field = asset_field(reel_id)
        caption, tags = editorial.get(reel_id, ("", ""))
        media = (f'<video controls playsinline preload="none" src="/reel/{reel_id}"></video>'
                 if (pipeline.directory / f"{reel_id}.mp4").is_file() else
                 '<div class="no-media">Preview unavailable</div>')
        actions = ""
        if state == "review":
            actions += form("approve", field, "Approve") + form("reject", field, "Reject")
        if state in ("review", "rejected", "failed", "queued"):
            if state != "queued":
                actions += form("regenerate", field, "Regenerate")
            actions += form("delete", field, "Delete")
        edit = ""
        if reel_id in editorial:
            edit = '<details><summary>Edit caption</summary>' + form(
                "edit", field + f'<label>Caption<textarea name="caption">{escape(caption)}</textarea></label>'
                f'<label>Hashtags<input name="hashtags" value="{escape(tags)}"></label>',
                "Save caption and hashtags") + '</details>'
        cards.append(
            f'<article class="card reel-card" tabindex="0" data-reel="{reel_id}" data-state="{state}">'
            f'{media}<div><span class="badge">{escape(format_id or "Local preview")}</span> '
            f'<span class="badge">{escape(state)}</span><h2>Reel {reel_id}</h2>'
            f'<dl><dt>Clip</dt><dd>{escape(Path(source).name)}</dd>'
            f'<dt>Song</dt><dd>{escape(Path(song).name) if song else "—"}</dd></dl>'
            f'<p>{escape(caption or "No caption yet.")}</p><p>{escape(tags)}</p>'
            f'<p class="error">{escape(error or "")}</p>{edit}<div class="actions">{actions}</div></div></article>'
        )
    return ('<p class="muted">J / K Navigate · A Approve · R Reject · G Regenerate</p>'
            '<div class="review-grid">' + ("".join(cards) or empty("Make a reel to start reviewing.")) + '</div>')


def queue(pipeline, form, zone):
    ids = pipeline.posting_queue()
    if not ids:
        return empty("Approved reels will appear here.")
    slots = pipeline.settings()["posting_slots"].split(",")[:int(pipeline.settings()["volume"])]
    rows = []
    for index, reel_id in enumerate(ids):
        rows.append(f'<li class="card queue-row" draggable="true" data-id="{reel_id}">'
                    f'<span class="grip" aria-hidden="true">⠿</span><video muted preload="metadata" '
                    f'src="/reel/{reel_id}#t=0.1"></video><strong>Reel {reel_id}</strong>'
                    f'<span class="slot">{escape(slots[index] + " " + zone) if index < len(slots) else "Later"}</span>'
                    '<button type="button" data-move="-1" aria-label="Move up">↑</button>'
                    '<button type="button" data-move="1" aria-label="Move down">↓</button></li>')
    return ('<p class="muted">Drag reels or use the arrow buttons, then save the order.</p>'
            f'<ol id="queue-list" data-slots="{escape(",".join(slots))}" data-zone="{escape(zone)}">' + "".join(rows) + '</ol>' +
            form("reorder", f'<input type="hidden" name="order" value="{",".join(map(str, ids))}">', "Save posting order"))


def libraries(pipeline, form):
    by_kind = {"song": [], "clip": []}
    for asset_id, path, kind, pinned, retired, peak in pipeline.library():
        media = (f'<video controls preload="metadata" playsinline src="/preview/{asset_id}#t=0.1"></video>'
                 if kind == "clip" else
                 f'<canvas class="wave" data-src="/waveform/{asset_id}" aria-label="Song waveform"></canvas>'
                 f'<audio controls preload="none" src="/preview/{asset_id}"></audio>')
        actions = ""
        if not retired:
            for action, label in (("pin", "Unpin" if pinned else "Pin"), ("retire", "Retire"),
                                  ("timestamp", "Detect timestamp")):
                actions += form("library-" + action, asset_field(asset_id), label)
        by_kind[kind].append(f'<article class="card">{media}<h3>{escape(Path(path).name)}</h3>'
                             f'<span class="badge">{"Retired" if retired else "Pinned" if pinned else "Active"}</span>'
                             f'<p>{f"Selected timestamp {peak:g}s" if peak is not None else "Timestamp not detected"}</p>'
                             f'<div class="actions">{actions}</div></article>')
    return "".join(f'<h2>{kind.title()}s</h2><div class="grid">'
                   + ("".join(cards) or empty(f"Add a {kind} to populate this library.")) + '</div>'
                   for kind, cards in by_kind.items())


def settings(pipeline, form, zone):
    values = pipeline.settings()
    slots = "".join(f'<label>Slot {i}<input data-slot type="time" required value="{escape(value)}">'
                    '<button type="button" data-remove-slot aria-label="Remove slot">×</button></label>'
                    for i, value in enumerate(values["posting_slots"].split(","), 1))
    modes = "".join(f'<label><input type="radio" name="mode" value="{value}" '
                    f'{"checked" if values["mode"] == value else ""}>{label}</label>'
                    for value, label in (("approval", "Require approval"), ("auto", "Auto-post")))
    weights = "".join(f'<label>{name}<input data-weight type="range" min="0" max="100" value="{escape(value)}">'
                      f'<output>{escape(value)}</output></label>'
                      for name, value in zip(("F1", "F2", "F3"), values["format_weights"].split(",")))
    cadence = "".join(f'<option {"selected" if value == values["refresh_cadence"] else ""}>{value}</option>'
                     for value in ("weekly", "monthly"))
    fields = (f'<input type="hidden" name="posting_slots" value="{escape(values["posting_slots"])}">'
              f'<input type="hidden" name="format_weights" value="{escape(values["format_weights"])}">'
              f'<fieldset><legend>Posting slots ({escape(zone)})</legend><div id="slots">{slots}</div>'
              '<button type="button" data-add-slot>Add slot</button></fieldset>'
              f'<label>Daily volume<input type="number" min="1" name="volume" value="{escape(values["volume"])}" required></label>'
              f'<fieldset><legend>Publishing</legend><div class="segmented">{modes}</div></fieldset>'
              f'<label>Refresh cadence<select name="refresh_cadence">{cadence}</select></label>'
              f'<fieldset><legend>Format weights</legend>{weights}</fieldset><p class="inline-error" role="alert"></p>')
    return '<div class="card settings">' + form("settings", fields, "Save settings") + '</div>'


def sources(pipeline, report, form, can_retest):
    summaries = report.get("provider_summary", {})
    rows = []
    configured = pipeline.sites()
    sites = dict.fromkeys(sorted(summaries), True) | configured
    for name, enabled in sites.items():
        stats = [row for row in summaries.get(name, {}).values() if row.get("observed", 0)]
        observed = sum(row["observed"] for row in stats)
        verified = sum(row.get("verified", 0) for row in stats)
        label = "Unmeasured" if not observed else "Working" if verified else "No successes"
        meter = (f'<meter min="0" max="{observed}" value="{verified}"></meter> {verified}/{observed}'
                 if observed else "Awaiting probe results")
        badge = "good" if verified else "bad" if observed else ""
        rows.append(f'<article class="card"><h2>{escape(name)}</h2>'
                    f'<span class="badge {badge}">{label}</span><p>{meter}</p>'
                    + (form("site-toggle", f'<input type="hidden" name="site" value="{escape(name)}">',
                            "Disable" if enabled else "Enable") if name in configured else '') + '</article>')
    return ((form("site-retest", "", "Write retest job") if can_retest else "")
            + '<div class="grid">' + ("".join(rows) or empty("No sources configured.")) + '</div>')


def logs(pipeline):
    rows = list(pipeline.recent_actions())
    rows += [("—", f"Reel {key}", error or "Render failed")
             for key, state, error in pipeline.list_reels() if state == "failed"]
    content = "".join(f'<tr class="{"failure" if outcome != "accepted" else ""}">'
                      + "".join(f'<td>{escape(str(value))}</td>' for value in (when, action, outcome)) + '</tr>'
                      for when, action, outcome in rows)
    return ('<h2>Logs and failures</h2><label>Filter logs<input id="log-filter" type="search"></label>'
            '<div class="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Action</th><th>Result</th></tr>'
            f'</thead><tbody id="log-rows">{content}</tbody></table></div>'
            + (empty("Dashboard actions and render failures will appear here.") if not rows else ""))

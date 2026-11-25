#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ $EUID -ne 0 ]] || { echo "Run as a non-root user" >&2; exit 2; }
[[ $# == 3 ]] || { echo "Usage: server_runner.sh QUEUE_CLONE PROBE_CHECKOUT TOKEN_FILE" >&2; exit 2; }
queue=$(realpath "$1")
probe=$(realpath "$2")
token=$(realpath "$3")
[[ "$queue" != "$probe" && -f "$token" && -O "$token" && $(stat -c %a "$token") == 600 ]]
[[ $(git -C "$queue" remote get-url origin) == https://github.com/Manas236/Resolve.git ]]
[[ -x "$probe/.venv/bin/python" && -f "$probe/tools/m0_probe.py" ]]
state="$queue/.git/resolve-runner"
mkdir -p "$state"
exec 9>"$state/lock"
flock -n 9
cd "$queue"
git_remote() {
    timeout 60 git -c credential.helper= -c 'credential.helper=!f() { if [ "$1" = get ]; then printf "username=x-access-token\npassword="; cat "$RESOLVE_TOKEN_FILE"; printf "\n"; fi; }; f' "$@"
}
export RESOLVE_TOKEN_FILE="$token" GIT_TERMINAL_PROMPT=0
run_job() (
    set -e
    git_remote -c core.hooksPath=/dev/null pull --rebase origin main
    [[ ! -f jobs/pc-acquisition ]] || exit 0
    [[ -f jobs/next_job.json ]] || exit 0
    job=$("$probe/.venv/bin/python" - jobs/next_job.json <<'PY'
import json
import re
import sys
from pathlib import Path

with Path(sys.argv[1]).open(encoding="utf-8") as stream:
    job = json.loads(stream.read(4096))
required = {"id", "limit", "no_browser"}
if not isinstance(job, dict) or not required <= set(job) <= required | {"providers"}:
    raise SystemExit("Invalid job schema")
if not isinstance(job["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", job["id"]):
    raise SystemExit("Invalid job ID")
if type(job["limit"]) is not int or not 1 <= job["limit"] <= 10 or type(job["no_browser"]) is not bool:
    raise SystemExit("Invalid job options")
providers = job.get("providers")
if providers is not None and (
    not isinstance(providers, list) or not 1 <= len(providers) <= 9
    or any(name not in ("mp7", "cnvmp3", "ythd", "piped", "invidious", "cobalt",
                        "soundcloud", "deezer", "itunes") for name in providers)
    or len(set(providers)) != len(providers)
):
    raise SystemExit("Invalid diagnostic providers")
print(job["id"], job["limit"], int(job["no_browser"]), *(providers or []))
PY
)
    read -r -a options <<< "$job"
    id="${options[0]}" limit="${options[1]}" no_browser="${options[2]}"
    output="research/server_jobs/$id.json"
    if git cat-file -e "origin/main:$output" 2>/dev/null; then exit 0; fi
    if [[ -f "$state/$id.json" ]]; then
        echo "Resuming report publication for $id"
    else
        if [[ "$id" == m0-browser-diagnostic-03 || "$id" == m0-route-diagnostic-04 ]] &&
           ! git -C "$probe" merge-base --is-ancestor 4260e103cc07262d73d2ea640ee356ebf298404c HEAD 2>/dev/null; then
            echo "Diagnostic 03 needs the reviewed replay parser at 4260e10 or later. Update the probe checkout first." >&2
            exit 1
        fi
        if [[ -n "$(git -C "$probe" status --porcelain -- tools/m0_probe.py research/m0_corpus.json)" ]]; then
            echo "Commit or restore the locally modified probe or corpus before running a server job." >&2
            exit 1
        fi
        if [[ "$id" == m0-route-diagnostic-04 ]] &&
           ! git -C "$probe" grep -q '^def probe_api_route(' HEAD -- tools/m0_probe.py; then
            echo "Update the reviewed probe checkout to include the alternative routes." >&2
            exit 1
        fi
        flags=()
        if [[ "$no_browser" == 1 ]]; then flags+=(--no-browser); fi
        if (( ${#options[@]} > 3 )); then flags+=(--providers "${options[@]:3}"); fi
        (cd "$probe"
         env -u RESOLVE_TOKEN_FILE -u GEMINI_API_KEY timeout --kill-after=15s 3h .venv/bin/python tools/m0_probe.py --limit "$limit" "${flags[@]}" --output "$state/$id.partial"
        ) 9>&-
        "$probe/.venv/bin/python" -c 'import json,sys; from pathlib import Path; sys.exit(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("complete") is not True)' "$state/$id.partial"
        mv "$state/$id.partial" "$state/$id.json"
    fi
    if git diff --quiet origin/main HEAD -- "$output"; then
        mkdir -p research/server_jobs
        install -m 600 "$state/$id.json" "$output"
        git add -- "$output"
        git commit --only -m "Record Resolve server probe $id" -- "$output"
    fi
    git_remote push origin HEAD:main
    rm -f "$state/$id.json"
)
while true; do
    (run_job) &
    wait "$!" || printf '%s Resolve job failed; retrying after poll interval\n' "$(date -Is)" >&2
    [[ ! -f jobs/pc-acquisition ]] || exit 0
    sleep 900
done

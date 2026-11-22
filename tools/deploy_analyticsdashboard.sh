#!/usr/bin/env bash
# Prepare or verify the scoped Chromium profile on AnalyticsDashboard.
set -euo pipefail

profile=/etc/apparmor.d/playwright-chromium
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-}" in
  install-profile)
    if [[ "$EUID" != 0 ]]; then
      echo "Run the profile installation as root via non-interactive sudo." >&2
      exit 1
    fi
    if [[ "$(getent passwd ubuntu | cut -d: -f6)" != /home/ubuntu ]]; then
      echo "Expected the AnalyticsDashboard ubuntu account at /home/ubuntu." >&2
      exit 1
    fi
    temp="$(mktemp)"
    trap 'rm -f -- "$temp"' EXIT
    cat > "$temp" <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
profile playwright-chromium /home/ubuntu/.cache/ms-playwright/chromium*/**/chrome* flags=(unconfined) {
  userns,
}
PROFILE
    apparmor_parser -Q -T "$temp"
    if [[ -e "$profile" ]] && ! cmp -s "$temp" "$profile"; then
      echo "A different profile already exists at $profile. Verify it locally before replacing." >&2
      exit 1
    fi
    if [[ ! -e "$profile" ]]; then
      install -o root -g root -m 0644 "$temp" "$profile"
    fi
    apparmor_parser -r "$profile"
    ;;
  verify)
    if [[ "$(id -un)" != ubuntu ]]; then
      echo "Verify using the unprivileged AnalyticsDashboard ubuntu account." >&2
      exit 1
    fi
    exec "$root/.venv/bin/python" "$root/tools/healthcheck.py" --analyticsdashboard
    ;;
  *)
    echo "Usage: bash tools/deploy_analyticsdashboard.sh {install-profile|verify}" >&2
    exit 2
    ;;
esac

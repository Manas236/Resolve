"""Check Resolve's local media runtime, registry evidence and Chromium sandbox."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


def registry_health(report):
    summary = report.get("provider_summary", {})
    if not isinstance(summary, dict):
        raise ValueError("Invalid provider summary")
    counts = {"audio": 0, "video": 0}
    for provider in summary.values():
        for kind in counts:
            row = provider.get(kind, {})
            if row.get("verified", 0) > 0:
                counts[kind] += 1
    return counts


def checks(root, report_path, *, analyticsdashboard=False):
    statuses = {}

    def check(name, probe):
        try:
            statuses[name] = {"ok": True, "detail": probe()}
        except (OSError, ValueError, RuntimeError, TypeError, AttributeError,
                subprocess.SubprocessError) as error:
            statuses[name] = {"ok": False, "detail": str(error)}

    def binary(name):
        if not shutil.which(name):
            raise RuntimeError(f"Install {name}")
        return subprocess.check_output([name, "-version"], text=True, timeout=10).splitlines()[0]

    def registry():
        count = registry_health(json.loads(report_path.read_text(encoding="utf-8")))
        if min(count.values()) < 3:
            raise RuntimeError(f"Last available report has verified audio/video providers: {count}")
        return count

    def profile():
        path = Path("/sys/kernel/security/apparmor/profiles")
        try:
            profiles = path.read_text(encoding="utf-8")
        except PermissionError:
            profiles = subprocess.check_output(
                ["sudo", "-n", "cat", str(path)], text=True, timeout=10,
            )
        if not any(line.startswith("playwright-chromium (") for line in profiles.splitlines()):
            raise RuntimeError("playwright-chromium AppArmor profile is not loaded")
        return "playwright-chromium loaded"

    def chromium():
        result = subprocess.run([sys.executable, str(root / "tools/m0_probe.py"), "--check-browser"],
                                text=True, capture_output=True, timeout=35)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Chromium failed")
        return result.stdout.strip()

    check("ffmpeg", lambda: binary("ffmpeg"))
    check("ffprobe", lambda: binary("ffprobe"))
    check("registry", registry)
    if analyticsdashboard:
        check("apparmor", profile)
    check("chromium", chromium)
    return statuses


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=root / "research/m0_server_results.json")
    parser.add_argument("--analyticsdashboard", action="store_true")
    args = parser.parse_args()
    results = checks(root, args.report, analyticsdashboard=args.analyticsdashboard)
    print(json.dumps(results, indent=2))
    return int(not all(item["ok"] for item in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())

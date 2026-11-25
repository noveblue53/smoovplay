"""The README's download count: setup downloads from GitHub's live counts plus the counts from before they were reset
(.github/download-baseline.json), written for a shields.io endpoint badge.

    python .github/scripts/download_count.py STATS_DIR

STATS_DIR is a checkout of the `stats` branch (empty the first time). When the total has changed (against the last
line of history.csv), it rewrites downloads.json (the badge, in shields.io's endpoint format with nothing else in
it) and adds a line to history.csv; otherwise it leaves both alone, so there's nothing to commit. Counts SmoovPlay-Setup.exe only: new installs and in-app updates, not people. Uses GITHUB_TOKEN if set."""
import csv
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.request

SETUP = "SmoovPlay-Setup.exe"
REPO = os.environ.get("GITHUB_REPOSITORY", "smoovplay/smoovplay")
BASELINE = pathlib.Path(__file__).resolve().parent.parent / "download-baseline.json"


def live_counts() -> dict[str, int]:
    """Setup downloads per published release, as GitHub counts them now."""
    out, page = {}, 1
    while True:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=100&page={page}",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "smoovplay-stats"})
        if os.environ.get("GITHUB_TOKEN"):
            req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
        with urllib.request.urlopen(req, timeout=30) as r:
            releases = json.load(r)
        for rel in releases:
            setups = [a["download_count"] for a in rel["assets"] if a["name"] == SETUP]
            if setups and not rel.get("draft"):  # source-only pre-releases (community-preview-1) have no setup
                out[rel["tag_name"]] = sum(setups)
        if len(releases) < 100:
            return out
        page += 1


def short(n: int) -> str:
    """2103 -> 2.1k, 12345 -> 12k, 987 -> 987."""
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    if n < 1_000_000:
        return f"{n // 1000}k"
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


def main(stats_dir: str) -> int:
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["setup_downloads"]
    live = live_counts()
    total = sum(base.values()) + sum(live.values())
    d = pathlib.Path(stats_dir)
    badge, hist = d / "downloads.json", d / "history.csv"
    rows = list(csv.reader(hist.open(newline="", encoding="utf-8"))) if hist.exists() else []
    before = int(rows[-1][1]) if len(rows) > 1 else None
    if before == total:
        print(f"downloads: {total:,}, unchanged")
        return 0
    badge.write_text(json.dumps({"schemaVersion": 1, "label": "downloads", "message": short(total),
                                 "color": "3A4250", "cacheSeconds": 3600}, indent=2) + "\n",
                     encoding="utf-8")
    new = not hist.exists()
    with hist.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_utc", "setup_downloads", "by_release"])
        tags = sorted(set(base) | set(live), key=lambda t: [int(x) for x in re.findall(r"\d+", t.split("-")[0])],
                      reverse=True)  # newest version first; a tag that isn't one (community-preview-1) goes last
        w.writerow([dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), total,
                    ";".join(f"{t}={base.get(t, 0) + live.get(t, 0)}" for t in tags)])
    print(f"downloads: {before if before is not None else '-'} -> {total:,} ({short(total)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

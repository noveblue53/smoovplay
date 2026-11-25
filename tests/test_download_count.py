"""The README's download count (.github/scripts/download_count.py): it counts setup downloads per release, and a
release that isn't a version (the source-only community-preview-1) mustn't stop it."""
import csv
import importlib.util
import io
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "download_count.py"


def _script(monkeypatch, releases):
    spec = importlib.util.spec_from_file_location("download_count", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(releases).encode()))
    return mod


def _release(tag, setup=None, draft=False, prerelease=False):
    assets = [{"name": "notes.txt", "download_count": 50}]
    if setup is not None:
        assets.append({"name": "SmoovPlay-Setup.exe", "download_count": setup})
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": assets}


def test_only_published_releases_with_a_setup_are_counted(monkeypatch):
    mod = _script(monkeypatch, [_release("community-preview-1", prerelease=True), _release("v0.3.14", 0, draft=True),
                                _release("v0.3.13", 7), _release("v0.3.12", 0)])
    assert mod.live_counts() == {"v0.3.13": 7, "v0.3.12": 0}


def test_the_history_lists_versions_newest_first_and_any_other_tag_last(tmp_path, monkeypatch):
    mod = _script(monkeypatch, [_release("community-preview-2", 1, prerelease=True), _release("v0.3.9", 3),
                                _release("v0.3.13", 7), _release("v0.4.0-beta.1", 2, prerelease=True)])
    assert mod.main(str(tmp_path)) == 0
    header, row = list(csv.reader((tmp_path / "history.csv").open(newline="", encoding="utf-8")))
    assert header == ["time_utc", "setup_downloads", "by_release"]
    tags = [part.split("=")[0] for part in row[2].split(";")]
    assert tags[:3] == ["v0.4.0-beta.1", "v0.3.13", "v0.3.9"] and tags[-1] == "community-preview-2"
    baseline = json.loads((SCRIPT.parent.parent / "download-baseline.json").read_text(encoding="utf-8"))
    assert int(row[1]) == sum(baseline["setup_downloads"].values()) + 1 + 3 + 7 + 2
    assert json.loads((tmp_path / "downloads.json").read_text(encoding="utf-8"))["label"] == "downloads"

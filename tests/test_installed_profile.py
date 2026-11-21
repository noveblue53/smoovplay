"""Settings and per-user paths seen by a fresh Python process launched by the installer."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "xbox-4070ti.json"


@pytest.mark.parametrize("fork_data,explicit_settings", [(False, False), (True, False), (True, True)])
def test_installed_profile_paths_and_settings(tmp_path, fork_data, explicit_settings):
    appdata = tmp_path / "app data"
    env = dict(os.environ, APPDATA=str(appdata))
    for key in ("SMOOVPLAY_DATA_DIR", "SMOOVPLAY_SETTINGS", "SMOOVPLAY_LOOKS_DIR"):
        env.pop(key, None)
    data = appdata / "framegen"
    if fork_data:
        data = tmp_path / "fork data"
        env["SMOOVPLAY_DATA_DIR"] = str(data)
    settings = data / "settings.json"
    if explicit_settings:
        settings = tmp_path / "custom profile.json"
        env["SMOOVPLAY_SETTINGS"] = str(settings)

    # Seed the committed profile at exactly the location the launcher chooses.
    settings.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PROFILE, settings)
    result = subprocess.run(
        [sys.executable, "-c", """
import json
from framegen import looks, replay, settings
from framegen.user_paths import data_dir

print(json.dumps({
    "paths": [str(data_dir()), str(settings.DEFAULT_PATH), str(replay.SAVING_DIR), str(looks.looks_dir())],
    "values": settings.Settings.load().values(),
}))
"""],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )
    actual = json.loads(result.stdout)
    assert list(map(Path, actual["paths"])) == [data, settings, data / "saving", data / "looks"]
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    # Guard both the profile contents and that Settings consumed it (full motion is non-default).
    assert (profile["hz"], profile["model"], profile["scaler"], profile["motion"]) == (60, "4.22.lite", "fsr", "full")
    assert {key: actual["values"][key] for key in profile} == profile

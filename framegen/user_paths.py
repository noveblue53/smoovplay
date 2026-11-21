"""Per-user data shared by the pipeline and its launcher."""
import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("SMOOVPLAY_DATA_DIR")
    return Path(override) if override else Path(os.environ.get("APPDATA", Path.home())) / "framegen"

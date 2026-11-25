"""The launcher starts every Python with the user's own site-packages left out (Proc.Python in Core.cs). The
embeddable Python's isolated mode doesn't do that by itself: with a regular Python 3.12 on the PC, pip took the
user's packages as part of the runtime and uninstalled the user's copies. (C#, so this reads the source.)"""
import re
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "installer" / "launcher" / "Core.cs"


def _proc_python() -> str:
    src = CORE.read_text(encoding="utf-8")
    head = r"public static ProcessStartInfo Python\(string python, string args, string cwd\)"
    m = re.search(head + r"\s*\{(.*?)\n        \}", src, re.S)
    assert m, "Proc.Python not found in Core.cs"
    return m.group(1)


def test_every_python_the_launcher_starts_leaves_out_the_user_site():
    body = _proc_python()
    args = re.search(r'new ProcessStartInfo\(python, "([^"]*)" \+ args\)', body)
    assert args and "-s" in args.group(1).split()  # this Python
    assert 'EnvironmentVariables["PYTHONNOUSERSITE"] = "1"' in body  # and those it starts (pip, subprocesses)


def test_nothing_else_starts_the_runtime_python_directly():
    """Every other Python start goes through Proc.Python, so it gets -s too."""
    for cs in CORE.parent.glob("*.cs"):
        for line in cs.read_text(encoding="utf-8").splitlines():
            if "new ProcessStartInfo(" in line and "Python" in line.split("new ProcessStartInfo(")[1].split(",")[0]:
                raise AssertionError(f"{cs.name} starts Python itself: {line.strip()}")
            if "new ProcessStartInfo(python," in line:
                assert '"-u -X utf8 -s "' in line, line.strip()

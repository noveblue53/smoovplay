"""Summarises scripts/presentmon_check.ps1's PresentMon CSVs: how SmoovPlay's frames reached the screen.

    .venv\\Scripts\\python scripts\\presentmon_summary.py out\\presentmon

Per session (windowed, fullscreen, windowed-vsync, and dxgi-* for Window presentation: Low delay):
  - present mode: "Hardware: Independent Flip" / "Hardware Composed: Independent Flip" go straight to the screen;
    "Composed: Flip" / "Composed: Copy..." go through the Windows compositor, which adds about a refresh.
  - until displayed: from our Present call to the frame reaching the screen (median / 95th percentile).
  - dropped: presented frames that were never shown (replaced before the screen got to them).
  - between display changes: how evenly frames reached the screen.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter
from pathlib import Path, PureWindowsPath

import numpy as np

UNTIL_DISPLAYED = ("MsUntilDisplayed", "msUntilDisplayed")
BETWEEN_DISPLAY = ("MsBetweenDisplayChange", "msBetweenDisplayChange")
BETWEEN_PRESENTS = ("MsBetweenPresents", "msBetweenPresents")


def col(row: dict, names: tuple) -> float | None:
    for n in names:
        if n in row:
            try:
                value = float(row[n])
                return value if math.isfinite(value) else None
            except (TypeError, ValueError):
                return None
    return None


def summarise(path: Path, process: str = "python.exe", chain: str | None = None) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:  # PresentMon 2.x writes a BOM
        rows = [r for r in csv.DictReader(f)
                if PureWindowsPath(r.get("Application", "")).name.casefold() == process.casefold()
                and (chain is None or r.get("SwapChainAddress", "").casefold() == chain.casefold())]
    if not rows:
        return [f"{path.stem}: no frames from {process}" + (f" on {chain}" if chain else "")]
    # An executable can have multiple processes, each with its own swap-chain address space.
    chains = Counter((r.get("ProcessID", ""), r.get("SwapChainAddress", "")) for r in rows)
    (pid, selected), _ = chains.most_common(1)[0]
    rows = [r for r in rows if (r.get("ProcessID", ""), r.get("SwapChainAddress", "")) == (pid, selected)]
    modes = Counter(r.get("PresentMode", "?") for r in rows)
    v2 = "DisplayLatency" in rows[0]
    if not any(k in rows[0] for k in (*UNTIL_DISPLAYED, "DisplayedTime")):
        return [f"{path.stem}: no display tracking columns for {process}; capture with display tracking enabled"]
    if "Dropped" in rows[0]:  # v1: discarded rows can have numeric zero in both display time columns
        shown = [r for r in rows if r["Dropped"] == "0"]
    elif "DisplayedTime" in rows[0]:  # v2: NA when this frame was discarded
        shown = [r for r in rows if col(r, ("DisplayedTime",)) is not None]
    else:
        shown = [r for r in rows if col(r, UNTIL_DISPLAYED) is not None]
    latency = ("DisplayLatency",) if v2 else UNTIL_DISPLAYED
    until = [v for r in shown if (v := col(r, latency)) is not None]
    pacing = ("DisplayedTime",) if v2 else BETWEEN_DISPLAY
    between = [v for r in shown if (v := col(r, pacing)) is not None and v > 0]
    presents = [v for r in rows if (v := col(r, BETWEEN_PRESENTS)) is not None and v > 0]
    dropped = len(rows) - len(shown)
    pct = lambda xs, p: float(np.percentile(xs, p)) if xs else float("nan")  # noqa: E731
    runtime = rows[0].get("PresentRuntime", rows[0].get("Runtime", "?"))
    lines = [f"{path.stem}: {process} PID {pid}, swap chain {selected}; {len(rows)} presents, runtime {runtime}, "
             f"tearing allowed {rows[0].get('AllowsTearing', '?')}, sync interval {rows[0].get('SyncInterval', '?')}"]
    lines.append("  present mode: " + ", ".join(f"{m} {100 * n / len(rows):.0f}%" for m, n in modes.most_common()))
    if until:
        start = "CPU frame start" if v2 else "Present call"
        lines.append(f"  {start} until displayed: median {pct(until, 50):.1f} ms, p95 {pct(until, 95):.1f} ms")
    lines.append(f"  dropped (never shown): {dropped} ({100 * dropped / len(rows):.1f}%)")
    if presents:
        lines.append(f"  presents: every {pct(presents, 50):.2f} ms (median) = {1000 / pct(presents, 50):.0f} per second")
    if between:
        lines.append(f"  between display changes: median {pct(between, 50):.2f} ms, p5 {pct(between, 5):.2f}, "
                     f"p95 {pct(between, 95):.2f}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a PresentMon CSV or directory of sessions")
    parser.add_argument("path", type=Path, nargs="?", default=Path("out/presentmon"))
    parser.add_argument("--process", default="python.exe", help="presenting executable, e.g. XboxPcApp.exe")
    parser.add_argument("--chain", help="swap chain address (defaults to the busiest for this process)")
    args = parser.parse_args()
    csvs = [args.path] if args.path.is_file() else [p for p in sorted(args.path.glob("*.csv"))
                                                   if not p.stem.endswith("-ticks")]
    if not csvs:
        print(f"no PresentMon CSVs in {args.path}")
        return 1
    for p in csvs:
        print("\n".join(summarise(p, args.process, args.chain)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

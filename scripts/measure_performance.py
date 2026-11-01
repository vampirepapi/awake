"""Measure a running Awake process: CPU, memory and (on macOS) physical footprint.

Usage:
    python scripts/measure_performance.py <pid> [seconds]

Requires psutil (in requirements-dev.txt). Startup time is logged by the app
itself as ``event=startup_complete seconds=...`` in awake.log.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

import psutil


def main() -> None:
    pid = int(sys.argv[1])
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    proc = psutil.Process(pid)
    proc.cpu_percent(None)
    samples = []
    for _ in range(seconds):
        time.sleep(1)
        samples.append(proc.cpu_percent(None))
    mem = proc.memory_info()
    print(f"samples:      {seconds} x 1s")
    print(f"CPU average:  {sum(samples) / len(samples):.2f}%   (max {max(samples):.1f}%)")
    print(f"RSS:          {mem.rss / 1e6:.1f} MB   (includes shared system frameworks)")
    print(f"threads:      {proc.num_threads()}")
    if sys.platform == "darwin" and shutil.which("footprint"):
        out = subprocess.run(["footprint", str(pid)], capture_output=True, text=True, check=False).stdout
        for line in out.splitlines():
            if "phys_footprint:" in line:
                print(f"footprint:    {line.split(':', 1)[1].strip()}   (what Activity Monitor reports as Memory)")
    elif sys.platform == "win32":
        print(f"private:      {proc.memory_full_info().private / 1e6:.1f} MB   (Task Manager 'Memory')")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
bash_xtrace_profiler.py

Profile a Bash xtrace (set -x) log that includes per-line timestamps and call depth
(indicated by leading '+' signs). Each line's delta-to-next is attributed to the
deepest "active" function recognized by a 'name():' marker at that depth.

This is a best-effort, sampling-by-line approximation—great for finding hotspots
in shell scripts, though not a precise profiler.

Log format expected (typical PS4 with timestamp):
    + 1771756019.666704560 bash -x -- pacman
    ++ 1771756019.675013832 dirname pacman
    + 1771756020.030635246 main
    + 1771756020.033911364 main(): setup
    ...

Assumptions:
- Leading '+' characters indicate depth (1-based).
- A function "becomes active" at a line that contains 'name():'.
- The time for a line is the difference to the next traced line’s timestamp.
- We attribute that delta to the deepest active function at that line’s depth.

Outputs:
- Console summary (top-N by total time).
- Optional CSV with all functions.
- Optional Markdown table (top-N).


"""

import argparse
import sys
import re
from collections import defaultdict, Counter
from typing import List, Tuple, Optional, Dict

LINE_RE = re.compile(
    r'^(?P<pluses>\++)\s+(?P<ts>\d+\.\d{6,})\s+(?P<rest>.*)$'
)
# Function marker like: name(): ...
FUNC_MARKER_RE = re.compile(r'(?P<name>[A-Za-z0-9_-]+)\(\):')

ParsedLine = Tuple[int, int, float, Optional[str], str]  # (idx, depth, ts, func, rest)


def read_lines(path: str) -> List[str]:
    if path == "-":
        data = sys.stdin.read()
        return data.splitlines(True)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def parse_log(lines: List[str]) -> List[ParsedLine]:
    parsed: List[ParsedLine] = []
    for i, raw in enumerate(lines):
        raw = raw.rstrip("\n")
        m = LINE_RE.match(raw)
        if not m:
            continue
        depth = len(m.group("pluses"))
        ts = float(m.group("ts"))
        rest = m.group("rest")
        fm = FUNC_MARKER_RE.search(rest)
        func = fm.group("name") if fm else None
        parsed.append((i, depth, ts, func, rest))
    return parsed


def attribute_time(parsed: List[ParsedLine]):
    """
    Walk through parsed lines, compute dt to next line and attribute dt to the
    deepest active function (by depth) whose latest 'name():' we’ve seen.
    """
    current_func_by_depth: Dict[int, str] = {}
    func_time = defaultdict(float)  # total seconds per function
    func_calls = Counter()

    def active_func(depth: int) -> Optional[str]:
        for d in range(depth, 0, -1):
            if d in current_func_by_depth:
                return current_func_by_depth[d]
        return None

    for idx, (i, depth, ts, func, rest) in enumerate(parsed):
        # If line declares a function marker, record it at this depth
        if func:
            current_func_by_depth[depth] = func
            func_calls[func] += 1

        # Time delta to next parsed line
        if idx + 1 < len(parsed):
            ts_next = parsed[idx + 1][2]
            dt = max(0.0, ts_next - ts)
        else:
            dt = 0.0

        af = active_func(depth)
        if af:
            func_time[af] += dt
        else:
            func_time["<no-func>"] += dt

    first_ts = parsed[0][2]
    last_ts = parsed[-1][2]
    wall = max(0.0, last_ts - first_ts)
    return func_time, func_calls, wall


def build_summary(func_time, func_calls, wall: float):
    """
    Build a list of tuples:
        (name, total_seconds, percent_of_wall, calls, avg_seconds_per_call)
    sorted by total_seconds desc.
    """
    summary = []
    for name, t in func_time.items():
        calls = func_calls.get(name, 0)
        avg = (t / calls) if calls else 0.0
        pct = (t / wall * 100) if wall > 0 else 0.0
        summary.append((name, t, pct, calls, avg))
    summary.sort(key=lambda x: (-x[1], x[0]))
    return summary


def print_top(summary, wall: float, top: int):
    print(f"Total wall time in log: {wall * 1000:.3f} ms")
    print("Function, Total_ms, Percent, Calls, Avg_ms_per_call")
    for name, t, pct, calls, avg in summary[:top]:
        print(f"{name},{t*1000:.3f},{pct:.2f}%,{calls},{avg*1000:.3f}")


def save_csv(summary, csv_path: str):
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("Function,Total_ms,Percent,Calls,Avg_ms_per_call\n")
        for name, t, pct, calls, avg in summary:
            f.write(f"{name},{t*1000:.6f},{pct:.4f},{calls},{avg*1000:.6f}\n")


def save_md(summary, wall: float, top: int, md_path: str):
    lines = []
    lines.append(f"Total wall time in log: {wall * 1000:.3f} ms\n")
    lines.append("| Rank | Function | Total (ms) | % of wall | Calls | Avg / call (ms) |")
    lines.append("|---:|---|---:|---:|---:|---:|")
    for rank, (name, t, pct, calls, avg) in enumerate(summary[:top], 1):
        lines.append(
            f"| {rank} | {name} | {t*1000:.3f} | {pct:.2f}% | {calls} | {avg*1000:.3f} |"
        )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(
        description="Attribute time deltas in a Bash xtrace log to functions."
    )
    ap.add_argument("logfile", help="Path to trace log (or '-' for stdin)")
    ap.add_argument("--top", type=int, default=15, help="Rows to show in console table")
    ap.add_argument("--csv", help="Write full function summary to CSV")
    ap.add_argument("--md", help="Write top-N table to Markdown")
    args = ap.parse_args()

    lines = read_lines(args.logfile)
    parsed = parse_log(lines)
    if not parsed:
        print("No matching lines found. Ensure your trace has timestamps and leading '+' depth markers.", file=sys.stderr)
        sys.exit(1)

    func_time, func_calls, wall = attribute_time(parsed)
    summary = build_summary(func_time, func_calls, wall)

    print_top(summary, wall, args.top)

    if args.csv:
        save_csv(summary, args.csv)
        print(f"[wrote] {args.csv}")
    if args.md:
        save_md(summary, wall, args.top, args.md)
        print(f"[wrote] {args.md}")


if __name__ == "__main__":
    main()
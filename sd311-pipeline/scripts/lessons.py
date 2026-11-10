#!/usr/bin/env python3
"""Lesson helper: see where you are, check your code, or unblock yourself.

    python scripts/lessons.py status          # every lesson: not started / your code / answer key
    python scripts/lessons.py check 2         # run lesson 2's tests strictly (stubs = failures)
    python scripts/lessons.py diff 2          # compare with the answer key (after you finish!)
    python scripts/lessons.py solve 2         # put the answer key in place (backs up your version)
    python scripts/lessons.py reset 2         # put the empty stubs back (backs up your version)

Each lesson is described by `lessons/_kit/lesson_<NN>.json`:

    {
      "lesson": 2,
      "title": "Labels and as-of-time features",
      "files": ["ml/sd311_core/labels.py", "ml/sd311_core/features.py"],
      "pytest": ["-m", "lesson2"],
      "extra_checks": [{"cmd": ["dbt", "build", "--target", "duckdb"], "cwd": "dbt"}],
      "minutes": {"read": 25, "build": 50, "check_and_explain": 15, "total": 90}
    }

Your stubs come from `lessons/_kit/stubs/<NN>/<path>`; the answer key lives in
`lessons/_kit/solutions/<NN>/<path>`. Peek only when stuck. Reading the key
after you pass the tests is a good way to compare approaches.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "lessons" / "_kit"
BACKUPS = ROOT / ".lesson_backup"


def load_manifests() -> dict[int, dict]:
    manifests = {}
    for path in sorted(KIT.glob("lesson_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        manifests[int(data["lesson"])] = data
    return manifests


def get(manifests: dict[int, dict], n: int) -> dict:
    if n not in manifests:
        sys.exit(f"No lesson {n}. Known lessons: {sorted(manifests)}")
    return manifests[n]


def kit_file(kind: str, n: int, rel: str) -> Path:
    return KIT / kind / f"{n:02d}" / rel


def file_state(n: int, rel: str) -> str:
    current = ROOT / rel
    if not current.exists():
        return "missing"
    text = current.read_bytes()
    stub, solution = kit_file("stubs", n, rel), kit_file("solutions", n, rel)
    if stub.exists() and text == stub.read_bytes():
        return "not started"
    if solution.exists() and text == solution.read_bytes():
        return "answer key"
    return "your code"


def cmd_status(manifests: dict[int, dict], _args) -> None:
    print(f"{'#':>2}  {'lesson':<44} {'time':>7}  state")
    for n, m in manifests.items():
        states = {file_state(n, rel) for rel in m["files"]}
        if states == {"not started"}:
            state = "not started"
        elif states == {"answer key"}:
            state = "answer key in place"
        elif "not started" in states:
            state = "in progress"
        else:
            state = "your code (run `check` to verify)"
        minutes = m.get("minutes", {}).get("total")
        t = f"{minutes} min" if minutes else "?"
        print(f"{n:>2}  {m['title'][:44]:<44} {t:>7}  {state}")


def run(cmd: list[str], cwd: Path) -> bool:
    print(f"\n$ {' '.join(cmd)}   (in {cwd.relative_to(ROOT) if cwd != ROOT else '.'})", flush=True)
    return subprocess.call(cmd, cwd=cwd) == 0


def cmd_check(manifests: dict[int, dict], args) -> None:
    m = get(manifests, args.n)
    pytest_cmd = [sys.executable, "-m", "pytest", "--lesson-strict", "-o", "addopts=", "-q"]
    ok = run([*pytest_cmd, *m["pytest"]], ROOT)
    for extra in m.get("extra_checks", []):
        cmd = [sys.executable if part == "{python}" else part for part in extra["cmd"]]
        ok = run(cmd, ROOT / extra.get("cwd", ".")) and ok
    if not os.environ.get("SD311_DATA_DIR") and not (ROOT / "ml" / "data" / "raw").exists():
        print(
            "\nNote: real-data checks need the raw files. Set SD311_DATA_DIR "
            "(see ml/README.md) or they print SKIPPED."
        )
    if ok:
        print(f"\nPASS: lesson {args.n} is done. Try `diff {args.n}` to compare with the key.")
    else:
        print("\nNOT YET: read the first failure above; the lesson file has hints.")
    sys.exit(0 if ok else 1)


def backup(n: int, files: list[str]) -> Path:
    dest = BACKUPS / f"{time.strftime('%Y%m%d-%H%M%S')}-lesson{n:02d}"
    for rel in files:
        src = ROOT / rel
        if src.exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
    return dest


def replace_with(kind: str, manifests: dict[int, dict], args) -> None:
    m = get(manifests, args.n)
    if not args.yes:
        answer = input(
            f"Replace {len(m['files'])} file(s) for lesson {args.n} with the {kind}? "
            "Your version is backed up first. [y/N] "
        )
        if answer.strip().lower() != "y":
            sys.exit("Cancelled.")
    dest = backup(args.n, m["files"])
    for rel in m["files"]:
        shutil.copy2(kit_file(kind, args.n, rel), ROOT / rel)
    print(f"Done. Backup of your previous version: {dest.relative_to(ROOT)}")


def cmd_diff(manifests: dict[int, dict], args) -> None:
    m = get(manifests, args.n)
    for rel in m["files"]:
        mine = (ROOT / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        key_path = kit_file("solutions", args.n, rel)
        key = key_path.read_text(encoding="utf-8").splitlines(keepends=True)
        diff = difflib.unified_diff(key, mine, f"answer-key/{rel}", f"yours/{rel}")
        sys.stdout.writelines(diff)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    for name in ("check", "diff", "solve", "reset"):
        p = sub.add_parser(name)
        p.add_argument("n", type=int)
        if name in ("solve", "reset"):
            p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = parser.parse_args()
    manifests = load_manifests()
    {
        "status": cmd_status,
        "check": cmd_check,
        "diff": cmd_diff,
        "solve": lambda m, a: replace_with("solutions", m, a),
        "reset": lambda m, a: replace_with("stubs", m, a),
    }[args.command](manifests, args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/unstage_cosmetic.py

Unstage (and revert) any currently-staged .po/.pot file whose only diff is
the `POT-Creation-Date` header line. Ported out of the nightly workflow's
inline bash/awk step so the manual version-bump script can produce the
same minimal, noise-free diffs -- previously only the workflow did this, so
a human running update_python_version.py and committing with `git commit -am`
would bake pure-timestamp churn into history, which then became a spurious
baseline for the *next* nightly diff too.

Scope is deliberately narrow: only *modified* .po/.pot files are considered.
Added, deleted, renamed and binary files (e.g. the contributor chart) are
never touched -- a binary diff has no +/- content lines, which would
otherwise look "cosmetic" and get silently reverted.

Usage:
    python scripts/unstage_cosmetic.py

Must be run in a git repo that already has changes staged (e.g. after
`git add --all`). Requires git >= 2.23 (`git restore`).
"""
from __future__ import annotations

import re
import subprocess
import sys

PO_SUFFIXES = (".po", ".pot")
# A changed line that is the POT-Creation-Date header, and nothing else.
COSMETIC_LINE = re.compile(r'^[+-]"POT-Creation-Date:')


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        sys.exit(f"`{' '.join(cmd)}` failed:\n{exc.stderr}")


def staged_modified_po_files() -> list[str]:
    # -z: NUL-separated, unquoted paths (safe for non-ASCII/special names).
    out = run(
        ["git", "diff", "--staged", "--name-only", "--diff-filter=M", "-z"]
    ).stdout
    return [p for p in out.split("\0") if p.endswith(PO_SUFFIXES)]


def has_only_cosmetic_diff(path: str) -> bool:
    """True if there is at least one changed content line and every one
    of them is the POT-Creation-Date header."""
    diff = run(["git", "diff", "--staged", "-U0", "--", path]).stdout
    changed = [
        line
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    return bool(changed) and all(COSMETIC_LINE.match(line) for line in changed)


def main() -> None:
    files = staged_modified_po_files()
    if not files:
        print("No staged .po/.pot modifications.")
        return

    cosmetic = [p for p in files if has_only_cosmetic_diff(p)]
    if not cosmetic:
        print("No cosmetic-only files to unstage.")
        return

    # Reset index and working tree to HEAD in one call.
    run(["git", "restore", "--source=HEAD", "--staged", "--worktree", "--"] + cosmetic)

    print(f"Unstaged {len(cosmetic)} file(s) with only POT-Creation-Date changes:")
    for path in cosmetic:
        print(f"  - {path}")


if __name__ == "__main__":
    main()

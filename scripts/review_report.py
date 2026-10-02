#!/usr/bin/env python3
"""
scripts/review_report.py

Builds the markdown body of the "translation review needed" issue from the
changes currently STAGED in git (run it after `git add --all` and
`unstage_cosmetic.py`). Writes an empty file if there is nothing to report.

Sections:
  - Summary
  - New PO files            (counts only; every string in them is untranslated)
  - Newly fuzzy strings     (old msgid -> new msgid, plus the current msgstr)
  - New untranslated strings (new msgids that matched nothing, not fuzzy)

"New" always means "not present in HEAD", so re-running never re-reports
strings from earlier syncs.

Requires: polib (pip install polib)
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import polib

# GitHub rejects issue bodies over 65,536 characters.
MAX_BODY = 60_000
FUZZY_PER_FILE = 10
UNTRANSLATED_PER_FILE = 25
MAX_LINE = 160


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def staged(diff_filter: str) -> list:
    out = git(
        "diff",
        "--staged",
        "--no-renames",
        "-z",
        "--name-only",
        f"--diff-filter={diff_filter}",
        "--",
        "*.po",
    )
    return [p for p in out.split("\0") if p]


def key(entry: polib.POEntry) -> tuple:
    return (entry.msgctxt, entry.msgid)


def live(po: polib.POFile) -> list:
    return [e for e in po if not e.obsolete]


def is_fuzzy(entry: polib.POEntry) -> bool:
    return "fuzzy" in entry.flags


def is_untranslated(entry: polib.POEntry) -> bool:
    if entry.msgid_plural:
        return not any(entry.msgstr_plural.values())
    return not entry.msgstr


def clip(text: str, limit: int = MAX_LINE) -> str:
    # Keep everything on one line and make sure we can't close a code fence.
    text = text.replace("\n", "\\n").replace("```", "'''")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------


def collect():
    new_files = []  # (path, number of strings)
    changes = []  # (path, newly_fuzzy, new_untranslated)

    for path in staged("A"):
        po = polib.pofile(path)
        new_files.append((path, len(live(po))))

    for path in staged("M"):
        old = polib.pofile(git("show", f"HEAD:{path}"))
        new = polib.pofile(path)
        old_live = live(old)
        old_keys = {key(e) for e in old_live}
        old_fuzzy = {key(e) for e in old_live if is_fuzzy(e)}

        newly_fuzzy = []
        new_untranslated = []
        for e in live(new):
            if is_fuzzy(e):
                if key(e) not in old_fuzzy:
                    newly_fuzzy.append(e)
            elif is_untranslated(e) and key(e) not in old_keys:
                new_untranslated.append(e)

        if newly_fuzzy or new_untranslated:
            changes.append((path, newly_fuzzy, new_untranslated))

    return new_files, changes


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def fuzzy_block(path: str, entries: list) -> str:
    lines = [f"### `{path}` ({len(entries)} new fuzzy)", "", "```diff"]
    for e in entries[:FUZZY_PER_FILE]:
        if e.previous_msgid:
            lines.append("- " + clip(e.previous_msgid))
        else:
            lines.append("  (previous msgid unavailable)")
        lines.append("+ " + clip(e.msgid))
        if e.msgstr:
            lines.append("  msgstr: " + clip(e.msgstr))
        lines.append("")
    if len(entries) > FUZZY_PER_FILE:
        lines.append(f"  … and {len(entries) - FUZZY_PER_FILE} more")
    lines.append("```")
    return "\n".join(lines) + "\n"


def untranslated_block(path: str, entries: list) -> str:
    lines = [f"### `{path}` ({len(entries)} new untranslated)", "", "```text"]
    lines += [clip(e.msgid) for e in entries[:UNTRANSLATED_PER_FILE]]
    if len(entries) > UNTRANSLATED_PER_FILE:
        lines.append(f"… and {len(entries) - UNTRANSLATED_PER_FILE} more")
    lines.append("```")
    return "\n".join(lines) + "\n"


class Body:
    """Accumulates blocks until the size budget runs out."""

    def __init__(self, limit: int):
        self.parts: list = []
        self.size = 0
        self.limit = limit
        self.omitted = 0

    def add(self, text: str) -> None:
        if self.size + len(text) > self.limit:
            self.omitted += 1
            return
        self.parts.append(text)
        self.size += len(text) + 1


def render(new_files: list, changes: list) -> str:
    fuzzy_total = sum(len(f) for _, f, _ in changes)
    untr_total = sum(len(u) for _, _, u in changes)
    if not (new_files or fuzzy_total or untr_total):
        return ""

    body = Body(MAX_BODY)

    summary = ["## Summary", ""]
    if new_files:
        strings = sum(n for _, n in new_files)
        summary.append(
            f"- 🆕 **{len(new_files)}** new PO files ({strings} strings to translate)"
        )
    if fuzzy_total:
        n = sum(1 for _, f, _ in changes if f)
        summary.append(f"- 🔍 **{fuzzy_total}** newly fuzzy strings in {n} files")
    if untr_total:
        n = sum(1 for _, _, u in changes if u)
        summary.append(f"- ✏️ **{untr_total}** new untranslated strings in {n} files")
    body.add("\n".join(summary) + "\n")

    if new_files:
        lines = [f"## 🆕 New PO files ({len(new_files)})"]
        lines += [f"- `{p}` ({n} strings)" for p, n in new_files]
        body.add("\n".join(lines) + "\n")

    if fuzzy_total:
        body.add(
            "## 🔍 Newly fuzzy strings\n\n"
            "The source text changed slightly, so the existing translation "
            "(`msgstr`) may need updating. `-` is the old source, `+` the new.\n"
        )
        for path, fuzzy, _ in changes:
            if fuzzy:
                body.add(fuzzy_block(path, fuzzy))

    if untr_total:
        body.add(
            "## ✏️ New untranslated strings\n\n"
            "Brand-new source strings with no close match to an existing "
            "translation.\n"
        )
        for path, _, untr in changes:
            if untr:
                body.add(untranslated_block(path, untr))

    if body.omitted:
        body.parts.append(
            f"_{body.omitted} section(s) omitted to fit GitHub's issue size "
            f"limit — see the sync commit's diff for the full list._\n"
        )
    return "\n".join(body.parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    new_files, changes = collect()
    report = render(new_files, changes)
    args.output.write_text(report, encoding="utf-8")
    print(
        f"Report: {len(report)} chars, {len(new_files)} new files, "
        f"{len(changes)} changed files with new strings"
    )


if __name__ == "__main__":
    main()

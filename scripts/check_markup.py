#!/usr/bin/env python3
"""
scripts/check_markup.py

Verify that Sphinx roles, inline literals, and format placeholders match
exactly between the English msgid and the Persian msgstr — nothing missing,
and nothing extra. Catches both the common review slip (translating or
dropping `:class:`int``-style markup, ``code`` spans, %s/{0} placeholders,
|substitution| refs) and the subtler mistake of adding a reference that
doesn't exist in the original (which Sphinx's own build treats as a hard
error under -W, e.g. "inconsistent term references in translated message").

Requires: pip install polib

Usage:
    python3 scripts/check_markup.py library/functions.po
    python3 scripts/check_markup.py tutorial/*.po
    python3 scripts/check_markup.py .          # recurse a whole directory
"""
import re
import sys
from collections import Counter
from pathlib import Path

import polib

PATTERNS = [
    ("sphinx role", re.compile(r":(?:\w+:)?[\w.-]+:`.*?`")),
    ("literal/code span", re.compile(r"``.*?``")),
    ("substitution ref", re.compile(r"\|[\w.-]+\|")),
    ("percent placeholder", re.compile(r"%\(\w+\)[a-zA-Z]|%[a-zA-Z]")),
    ("brace placeholder", re.compile(r"\{[^{}\s]*\}")),
]


def check_file(path: Path) -> int:
    problems = 0
    po = polib.pofile(str(path))
    for entry in po:
        if entry.obsolete or not entry.msgid or not entry.msgstr:
            continue  # obsolete entry, header, or still untranslated
        for label, pattern in PATTERNS:
            expected = Counter(pattern.findall(entry.msgid))
            found = Counter(pattern.findall(entry.msgstr))
            if expected == found:
                continue

            missing = list((expected - found).elements())
            extra = list((found - expected).elements())
            problems += 1
            loc = f" ({entry.occurrences[0][0]}:{entry.occurrences[0][1]})" if entry.occurrences else ""
            tag = " [fuzzy]" if entry.fuzzy else ""
            parts = []
            if missing:
                parts.append(f"missing {label}: {missing}")
            if extra:
                parts.append(f"extra {label} not in source: {extra}")
            print(f"{path}{loc}{tag}: {'; '.join(parts)}")
            print(f"    msgid : {entry.msgid[:100]}")
            print(f"    msgstr: {entry.msgstr[:100]}")
    return problems


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    files = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        files.extend(sorted(p.rglob("*.po"))) if p.is_dir() else files.append(p)

    total = sum(check_file(f) for f in files)

    if total:
        print(f"\n{total} markup mismatch(es) found.")
        sys.exit(1)
    print("No markup mismatches found.")


if __name__ == "__main__":
    main()
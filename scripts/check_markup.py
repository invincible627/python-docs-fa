#!/usr/bin/env python3
"""
scripts/check_markup.py

Verify that Sphinx roles, inline literals, and format placeholders in the
English msgid are preserved verbatim in the Persian msgstr. Catches the most
common review slip: translating or mangling `:class:`int``-style markup,
``code`` spans, %s/{0} placeholders, or |substitution| refs.

Usage:
    python3 scripts/check_markup.py library/functions.po
    python3 scripts/check_markup.py tutorial/*.po
    python3 scripts/check_markup.py .          # recurse a whole directory
"""
import re
import sys
from pathlib import Path

PATTERNS = [
    ("sphinx role", re.compile(r":[\w.-]+:`.*?`")),
    ("literal/code span", re.compile(r"``.*?``")),
    ("substitution ref", re.compile(r"\|[\w.-]+\|")),
    ("percent placeholder", re.compile(r"%\(\w+\)[a-zA-Z]|%[a-zA-Z]")),
    ("brace placeholder", re.compile(r"\{[^{}\s]*\}")),
]


def unescape(raw: str) -> str:
    """Undo PO string escaping (raw includes the surrounding quotes)."""
    inner = raw[1:-1]
    out = []
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "\\" and i + 1 < len(inner):
            nxt = inner[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_po(path: Path):
    """Yield (location, flags, msgid, msgstr) for each entry in a .po file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    i, n = 0, len(lines)

    def read_block(keyword_line):
        nonlocal i
        parts = [unescape(keyword_line.split(" ", 1)[1].strip())]
        i += 1
        while i < n and lines[i].strip().startswith('"'):
            parts.append(unescape(lines[i].strip()))
            i += 1
        return "".join(parts)

    while i < n:
        location, flags = "", []
        while i < n and lines[i].startswith("#"):
            if lines[i].startswith("#:"):
                location = lines[i][2:].strip()
            elif lines[i].startswith("#,"):
                flags = [f.strip() for f in lines[i][2:].split(",")]
            i += 1
        if i >= n or not lines[i].startswith("msgid"):
            i += 1
            continue

        msgid = read_block(lines[i])
        msgid_plural = read_block(lines[i]) if i < n and lines[i].startswith("msgid_plural") else None

        if i < n and lines[i].startswith("msgstr["):
            msgstrs = {}
            while i < n and lines[i].startswith("msgstr["):
                idx = int(lines[i][7:lines[i].index("]")])
                msgstrs[idx] = read_block(lines[i])
            yield location, flags, msgid, msgstrs.get(0, "")
            if msgid_plural is not None and 1 in msgstrs:
                yield location, flags, msgid_plural, msgstrs[1]
            continue

        msgstr = read_block(lines[i]) if i < n and lines[i].startswith("msgstr") else ""
        yield location, flags, msgid, msgstr


def check_file(path: Path) -> int:
    problems = 0
    for location, flags, msgid, msgstr in parse_po(path):
        if not msgid or not msgstr:
            continue  # header entry or still untranslated
        for label, pattern in PATTERNS:
            expected = pattern.findall(msgid)
            if not expected:
                continue
            missing = [tok for tok in expected if tok not in msgstr]
            if missing:
                problems += 1
                loc = f" ({location})" if location else ""
                tag = " [fuzzy]" if "fuzzy" in flags else ""
                print(f"{path}{loc}{tag}: missing {label}: {missing}")
                print(f"    msgid : {msgid[:100]}")
                print(f"    msgstr: {msgstr[:100]}")
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
#!/usr/bin/env python3
"""
scripts/translation_status.py

Report translation progress across .po files: how many strings are
translated, fuzzy, or untranslated per file, plus overall totals. Meant to
help a contributor quickly find files that need work.

Usage:
    python3 scripts/translation_status.py                    # whole repo, least-translated first
    python3 scripts/translation_status.py tutorial/           # just one directory
    python3 scripts/translation_status.py --only-incomplete   # hide fully-done files
    python3 scripts/translation_status.py --sort name
    python3 scripts/translation_status.py --format markdown > STATUS.md
"""
import argparse
import sys
from pathlib import Path


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


def parse_po_entries(path: Path):
    """Yield (flags, msgid, msgstrs) for each entry in a .po file."""
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
        flags = []
        while i < n and lines[i].startswith("#"):
            if lines[i].startswith("#,"):
                flags = [f.strip() for f in lines[i][2:].split(",")]
            i += 1
        if i >= n or not lines[i].startswith("msgid"):
            i += 1
            continue

        msgid = read_block(lines[i])
        if i < n and lines[i].startswith("msgid_plural"):
            read_block(lines[i])  # plural source not needed for counting

        msgstrs = []
        if i < n and lines[i].startswith("msgstr["):
            while i < n and lines[i].startswith("msgstr["):
                msgstrs.append(read_block(lines[i]))
        elif i < n and lines[i].startswith("msgstr"):
            msgstrs.append(read_block(lines[i]))

        yield flags, msgid, msgstrs


def file_stats(path: Path):
    translated = fuzzy = untranslated = 0
    for flags, msgid, msgstrs in parse_po_entries(path):
        if not msgid:
            continue  # header entry
        if "fuzzy" in flags:
            fuzzy += 1
        elif any(m.strip() for m in msgstrs):
            translated += 1
        else:
            untranslated += 1
    return translated, fuzzy, untranslated


def collect_files(paths):
    files = []
    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.po")))
        elif p.suffix == ".po":
            files.append(p)
    return files


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="*", default=["."],
                         help="Files or directories to scan (default: whole repo)")
    parser.add_argument("--sort", choices=["percent", "untranslated", "name"], default="percent",
                         help="Sort order (default: percent, least-translated first)")
    parser.add_argument("--only-incomplete", action="store_true",
                         help="Hide files that are already fully translated")
    parser.add_argument("--format", choices=["text", "markdown", "csv"], default="text")
    args = parser.parse_args()

    files = collect_files(args.paths)
    if not files:
        print("No .po files found.")
        sys.exit(1)

    rows = []
    for f in files:
        t, fz, u = file_stats(f)
        total = t + fz + u
        percent = (t / total * 100) if total else 100.0
        rows.append((str(f), t, fz, u, total, percent))

    if args.only_incomplete:
        rows = [r for r in rows if r[2] > 0 or r[3] > 0]

    if args.sort == "percent":
        rows.sort(key=lambda r: r[5])
    elif args.sort == "untranslated":
        rows.sort(key=lambda r: -r[3])
    else:
        rows.sort(key=lambda r: r[0])

    total_t = sum(r[1] for r in rows)
    total_fz = sum(r[2] for r in rows)
    total_u = sum(r[3] for r in rows)
    total_all = total_t + total_fz + total_u
    total_percent = (total_t / total_all * 100) if total_all else 100.0

    if args.format == "csv":
        print("file,translated,fuzzy,untranslated,total,percent")
        for path, t, fz, u, total, percent in rows:
            print(f"{path},{t},{fz},{u},{total},{percent:.1f}")
        print(f"TOTAL,{total_t},{total_fz},{total_u},{total_all},{total_percent:.1f}")
        return

    if args.format == "markdown":
        print("| File | Translated | Fuzzy | Untranslated | % done |")
        print("|---|---:|---:|---:|---:|")
        for path, t, fz, u, total, percent in rows:
            print(f"| `{path}` | {t} | {fz} | {u} | {percent:.1f}% |")
        print(f"| **TOTAL** | **{total_t}** | **{total_fz}** | **{total_u}** | **{total_percent:.1f}%** |")
        return

    name_width = max((len(r[0]) for r in rows), default=4)
    header = f"{'File':<{name_width}}  {'Translated':>10}  {'Fuzzy':>6}  {'Untranslated':>12}  {'% done':>7}"
    print(header)
    print("-" * len(header))
    for path, t, fz, u, total, percent in rows:
        print(f"{path:<{name_width}}  {t:>10}  {fz:>6}  {u:>12}  {percent:>6.1f}%")
    print("-" * len(header))
    print(f"{'TOTAL':<{name_width}}  {total_t:>10}  {total_fz:>6}  {total_u:>12}  {total_percent:>6.1f}%")
    print(f"\n{len(rows)} file(s) shown. "
          f"{sum(1 for r in rows if r[5] < 100)} file(s) not fully translated.")


if __name__ == "__main__":
    main()
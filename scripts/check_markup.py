#!/usr/bin/env python3
"""
scripts/check_markup.py

Verify Sphinx markup consistency between msgid and msgstr:
  - Roles like :term:`text <target>` — the *target* (or the whole role, if
    it has no explicit target) must match; the display text is expected to
    be translated, matching Sphinx's own translation convention.
  - Literal/code spans (``...``), substitution refs (|...|), and %s/{name}
    placeholders — these must match verbatim, since they're not prose.
  - Invalid C-string escape sequences in msgstr (a lone backslash followed
    by a character that isn't one of \\ " n t r f b a v) — these make
    `msgfmt` fail with "invalid control sequence" and must be found before
    they break a build.

Output is grouped by file, with a per-file mismatch count and a grand
total at the end.

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

ROLE_PATTERN = re.compile(r":((?:\w+:)?[\w.-]+):`([^`]+)`")
# Sphinx itself accepts `label<target>` (no space) as well as the more
# common `label <target>` -- both are valid RST role syntax, and the
# upstream English source uses the no-space form in a few places (e.g.
# ":pypi:`file built<blurb>`"). The space before "<" is therefore made
# optional here so those roles are recognized as having an explicit
# target instead of being treated as if the *entire* display text were
# the target (which produced impossible-to-match false positives, since
# no translation could ever reproduce the literal English phrase).
TARGET_PATTERN = re.compile(r"^(.*?)\s?<([^<>]+)>$")

LITERAL_PATTERNS = [
    ("literal/code span", re.compile(r"``.*?``")),
    ("substitution ref", re.compile(r"\|[\w.-]+\|")),
    ("percent placeholder", re.compile(r"%\(\w+\)[a-zA-Z]|%[a-zA-Z]")),
    ("brace placeholder", re.compile(r"\{[^{}\s]*\}")),
]

# Valid C-string escapes that gettext/msgfmt accept inside a quoted
# string. A backslash followed by anything else is what msgfmt rejects
# with "invalid control sequence". This mirrors the set the old
# autofix bash script treated as "leave alone": \\  \"  \n \t \r \f \b \a \v
VALID_ESCAPE_CHARS = set('\\"ntrfbav')

# One raw quoted-string line as it appears in the .po file. Matches
# both the first line of an entry, which carries a keyword prefix
# (msgid "..."  / msgstr "..." / msgstr[0] "..."), and bare continuation
# lines ("...") that follow it. Captured group is the RAW content
# between the quotes - escapes are NOT decoded, which is required for
# this check (see note below).
RAW_STRING_LINE = re.compile(
    r'^(?:msgid|msgstr(?:\[\d+\])?|msgctxt)?\s*"((?:[^"\\]|\\.)*)"\s*$'
)

DISPLAY_ONLY_ROLES = {"dfn", "kbd", "guilabel", "menuselection", "samp", "file"}

DFN_PATTERN = re.compile(r":dfn:`")
DFN_ANGLE = re.compile(r":dfn:`[^`]*<[^`>]+>`")

def find_invalid_escapes_in_raw(raw: str):
    """Scan raw (undecoded) quoted-string content left-to-right the way
    a C-string tokenizer would, consuming two characters whenever a
    backslash is seen, and return the invalid `\\X` sequences found.

    MUST run on raw file text, not on polib's .msgid/.msgstr - polib
    decodes \\\\ into a single \\ before Claude ever sees it, so scanning
    decoded text turns safe, doubled backslashes (\\\\d in the file,
    meaning a literal backslash-d) into false positives that look like
    a lone backslash followed by an invalid character."""
    invalid = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] == "\\":
            if i + 1 < n:
                nxt = raw[i + 1]
                if nxt not in VALID_ESCAPE_CHARS:
                    invalid.append("\\" + nxt)
                i += 2
                continue
            else:
                invalid.append("\\<end-of-line>")
                i += 1
                continue
        i += 1
    return invalid


def check_raw_escapes(path: Path):
    """Line-by-line scan of the raw file for invalid escape sequences,
    independent of polib. Returns a list of (line_no, line_text,
    invalid_list) for every quoted-string line with a problem."""
    findings = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        m = RAW_STRING_LINE.match(line.strip())
        if not m:
            continue
        invalid = find_invalid_escapes_in_raw(m.group(1))
        if invalid:
            findings.append((lineno, line.strip(), invalid))
    return findings


def extract_role_targets(text: str):
    """For each Sphinx role, return its target: the <target> anchor if
    present, otherwise the role's full display text. Display-only roles
    (like :dfn:) are skipped, since their text is translatable."""
    targets = []
    for role, body in ROLE_PATTERN.findall(text):
        if role in DISPLAY_ONLY_ROLES:
            continue
        m = TARGET_PATTERN.match(body)
        targets.append(m.group(2).strip() if m else body.strip())
    return targets


def check_file(path: Path):
    """Return a list of finding-dicts for this file (empty if none)."""
    results = []
    po = polib.pofile(str(path))

    for entry in po:
        if entry.obsolete or not entry.msgid or not entry.msgstr:
            continue  # obsolete entry, header, or still untranslated

        findings = []

        expected_targets = Counter(extract_role_targets(entry.msgid))
        found_targets = Counter(extract_role_targets(entry.msgstr))
        if expected_targets != found_targets:
            missing = list((expected_targets - found_targets).elements())
            extra = list((found_targets - expected_targets).elements())
            if missing:
                findings.append(f"missing role target(s): {missing}")
            if extra:
                findings.append(f"role target(s) not in source: {extra}")

        # --- :dfn: checks (display-only role, so compare count, not text) ---
        msgid_dfn_count = len(DFN_PATTERN.findall(entry.msgid))
        msgstr_dfn_count = len(DFN_PATTERN.findall(entry.msgstr))
        if msgid_dfn_count != msgstr_dfn_count:
            findings.append(
                f":dfn: count differs ({msgid_dfn_count} vs {msgstr_dfn_count})"
            )
        if DFN_ANGLE.search(entry.msgstr):
            findings.append(":dfn: contains <...>, which Sphinx renders literally")

        for label, pattern in LITERAL_PATTERNS:
            expected = Counter(pattern.findall(entry.msgid))
            found = Counter(pattern.findall(entry.msgstr))
            if expected != found:
                missing = list((expected - found).elements())
                extra = list((found - expected).elements())
                if missing:
                    findings.append(f"missing {label}: {missing}")
                if extra:
                    findings.append(f"extra {label} not in source: {extra}")

        if findings:
            loc = (
                f"{entry.occurrences[0][0]}:{entry.occurrences[0][1]}"
                if entry.occurrences
                else ""
            )
            results.append(
                {
                    "loc": loc,
                    "fuzzy": entry.fuzzy,
                    "findings": findings,
                    "msgid": entry.msgid,
                    "msgstr": entry.msgstr,
                }
            )
    return results


def print_file_group(path: Path, results):
    print(f"\n{'=' * 70}")
    print(f"{path}  ({len(results)} mismatch(es))")
    print("=" * 70)
    for r in results:
        tag = " [fuzzy]" if r["fuzzy"] else ""
        loc = f" ({r['loc']})" if r["loc"] else ""
        print(f"{loc}{tag}: {'; '.join(r['findings'])}")
        print(f"    msgid : {r['msgid'][:100]}")
        print(f"    msgstr: {r['msgstr'][:100]}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    files = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*.po") if ".git" not in f.parts))
        else:
            files.append(p)

    total = 0
    per_file_counts = []
    escape_total = 0
    escape_files = 0

    for f in files:
        results = check_file(f)
        if results:
            print_file_group(f, results)
            total += len(results)
            per_file_counts.append((f, len(results)))

        escape_findings = check_raw_escapes(f)
        if escape_findings:
            print(f"\n{'=' * 70}")
            print(f"{f}  ({len(escape_findings)} invalid escape sequence line(s))")
            print("=" * 70)
            for lineno, line, invalid in escape_findings:
                print(f"  line {lineno}: invalid {invalid}")
                print(f"    {line[:120]}")
            escape_total += len(escape_findings)
            escape_files += 1

    if total:
        print(f"\n{'=' * 70}")
        print("Summary by file (sorted by mismatch count, descending):")
        print("=" * 70)
        for f, count in sorted(per_file_counts, key=lambda x: -x[1]):
            print(f"  {count:4d}  {f}")
        print(
            f"\n{total} markup mismatch(es) found across {len(per_file_counts)} file(s)."
        )

    if escape_total:
        print(
            f"\n{escape_total} invalid escape sequence line(s) found across "
            f"{escape_files} file(s). These will make msgfmt fail with "
            f"'invalid control sequence' - fix the offending backslash "
            f"in each line above."
        )

    if total or escape_total:
        sys.exit(1)

    print("No markup mismatches found.")


if __name__ == "__main__":
    main()

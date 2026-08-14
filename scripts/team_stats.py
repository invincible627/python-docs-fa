#!/usr/bin/env python3
"""
scripts/team_stats.py

Compute a per-contributor "Translated Count" for TEAM.md by walking every
.po file and using ``git blame`` to attribute each translated msgstr to the
author who last changed it.

Contributors are identified by their git ``user.name``, which is public
information visible on every commit.  Names are mapped to their TEAM.md
handles through the ``NAME_ALIASES`` table (a git username is not always
the same as the handle used on GitHub/TEAM.md).

If two contributors share the same git username, a warning is printed with
a partially redacted email so the conflict can be resolved by asking one of
them to update their ``git config user.name``.

Mechanical commits are excluded:
  * bulk ``Sync translations with CPython`` syncs
  * header-only maintenance commits (``Update .po files``)
  * Transifex/bot import commits and bot accounts

Requires: pip install polib

Usage:
    python3 scripts/team_stats.py                    # report over the whole repo
    python3 scripts/team_stats.py tutorial/          # restrict to a directory
    python3 scripts/team_stats.py --update-teammd    # rewrite TEAM.md in-place
"""
import argparse
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import polib

REPO_ROOT = Path(__file__).resolve().parent.parent

BOT_NAME_RE = re.compile(
    r'github[^\w]*actions|\[bot\]|not committed yet', re.IGNORECASE)
BOT_EMAIL_RE = re.compile(
    r'github-actions|transifex|\[bot\]|@users\.noreply\.github\.com',
    re.IGNORECASE,
)
MECHANICAL_SUBJECT_RE = re.compile(
    r'^(?:sync\s+translations\s+with\s+cpython\b'
    r'|update\s+\.po\s+files(?:\s*\(\d+\))?\s*$'
    r'|update\s+farsi\s+translations\s+from\s+transifex\b)',
    re.IGNORECASE,
)

# git user.name -> TEAM.md User handle (lowercase keys).
NAME_ALIASES = {
    "sepehr rasouli": "sepehr-rs",
    "revisto": "Revisto",
    "alireza shabani": "Revisto",
    "alireza shabani (revisto)": "Revisto",
    "invincible627": "invincible627",
    "khosro": "khosro_o",
    "ramiz-22": "Ramiz_222",
    "aimer": "aimer.hs872",
}

# Default role used when a contributor is first added to TEAM.md.
# Handles that already have a row keep the role stored in TEAM.md; this
# table is only consulted for brand-new rows.
NEW_ROW_ROLES = {}

SKIP_DIRS = {".git", ".cpython-src", ".venv", "__pycache__", "venv"}

TEAMMD_ROW_RE = re.compile(
    r'^\|\s*(?P<user>[^|]+?)\s*\|\s*(?P<role>[^|]+?)\s*\|\s*'
    r'(?P<t>\d+(?:\s*\([^)]*\))?)\s*\|$'
)


# ---------------------------------------------------------------------------
# Privacy helper
# ---------------------------------------------------------------------------

def redact_email(email: str) -> str:
    """Return a partially redacted email for warning messages.

    ``someone@example.com`` → ``s*****e@e******.com``

    Only the local part and domain name are obscured; the TLD is kept so
    the domain type is still recognisable.
    """
    if "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)

    def _blur(s: str) -> str:
        if len(s) <= 2:
            return s[0] + "*"
        return s[0] + "*" * (len(s) - 2) + s[-1]

    if "." in domain:
        domain_name, tld = domain.rsplit(".", 1)
        redacted_domain = f"{_blur(domain_name)}.{tld}"
    else:
        redacted_domain = _blur(domain)

    return f"{_blur(local)}@{redacted_domain}"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _is_mechanical(subject: str) -> bool:
    return bool(MECHANICAL_SUBJECT_RE.search(subject))


def _is_bot(name: str, email: str) -> bool:
    return bool(BOT_NAME_RE.search(name) or BOT_EMAIL_RE.search(email))


def git_blame_porcelain(path: Path) -> dict[str, dict]:
    """Run ``git blame --porcelain`` and return a commit-hash → info map.

    Each value is a dict with keys: ``name``, ``email``, ``subject``, and
    ``lines`` (a set of 1-based line numbers blamed to that commit).
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "blame", "--porcelain",
         "--", str(path.resolve().relative_to(REPO_ROOT))],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return {}

    commits: dict[str, dict] = {}
    current_hash = None

    for raw_line in result.stdout.splitlines():
        # Commit header: "<40-char-hash> <orig-line> <result-line> [<num-lines>]"
        parts = raw_line.split()
        if len(parts) >= 3 and len(parts[0]) == 40 and parts[0].isalnum() and parts[1].isdigit() and parts[2].isdigit():
            h = parts[0]
            result_line = int(parts[2])
            current_hash = h
            if h not in commits:
                commits[h] = {"name": "", "email": "", "subject": "", "lines": set()}
            commits[h]["lines"].add(result_line)
        elif raw_line.startswith("author ") and current_hash:
            commits[current_hash]["name"] = raw_line[len("author "):].strip()
        elif raw_line.startswith("author-mail ") and current_hash:
            email = raw_line[len("author-mail "):].strip().strip("<>")
            commits[current_hash]["email"] = email.lower()
        elif raw_line.startswith("summary ") and current_hash:
            commits[current_hash]["subject"] = raw_line[len("summary "):]

    return commits


def check_name_collisions(blame: dict[str, dict]) -> None:
    """Warn if the same git username appears with more than one email address.

    This indicates two distinct people sharing a username, which would cause
    their counts to be merged incorrectly.  Ask the affected contributor to
    run ``git config user.name`` to pick a unique name.
    """
    name_to_emails: dict[str, set[str]] = defaultdict(set)
    for info in blame.values():
        name = info["name"]
        email = info["email"]
        if name and email and not _is_bot(name, email):
            name_to_emails[name].add(email)

    for name, emails in name_to_emails.items():
        if len(emails) > 1:
            redacted = " vs ".join(redact_email(e) for e in sorted(emails))
            print(
                f"  warning: username '{name}' is used by multiple authors "
                f"({redacted}) — counts may be merged incorrectly. "
                f"Ask one of them to update their git config user.name."
            )


def real_author_for_lines(
    blame: dict[str, dict],
    line_numbers: set[int],
) -> str | None:
    """Return the git username of the most recent real (non-bot, non-mechanical)
    author who touched any of ``line_numbers``, or None.

    ``git blame --porcelain`` outputs commits in file order, not
    chronologically.  We pick the candidate whose blamed lines have the
    highest line number as a proxy for recency, which avoids an extra
    ``git log`` call per entry and is accurate enough for string-level work.
    """
    best_name: str | None = None
    best_line: int = -1

    for info in blame.values():
        overlap = info["lines"] & line_numbers
        if not overlap:
            continue
        if _is_bot(info["name"], info["email"]) or _is_mechanical(info["subject"]):
            continue
        candidate_line = max(overlap)
        if candidate_line > best_line:
            best_line = candidate_line
            best_name = info["name"]

    return best_name


# ---------------------------------------------------------------------------
# .po file walking
# ---------------------------------------------------------------------------

def collect_files(paths: list[str]) -> list[Path]:
    files = []
    for arg in paths:
        p = Path(arg) if Path(arg).is_absolute() else REPO_ROOT / arg
        if p.is_dir():
            for f in sorted(p.rglob("*.po")):
                rel_parts = set(f.relative_to(REPO_ROOT).parts)
                if rel_parts & SKIP_DIRS:
                    continue
                if any(part.startswith(".") for part in f.parts):
                    continue
                files.append(f)
        elif p.suffix == ".po":
            files.append(p)
    return files


def msgstr_line_numbers(po_path: Path, entry: polib.POEntry) -> set[int]:
    """Return the 1-based line numbers that belong to an entry's msgstr.

    polib exposes ``entry.linenum`` (the msgid line).  We scan forward from
    there to find the msgstr block, collecting every continuation line too.
    """
    lines = po_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    i = entry.linenum  # 1-based → use as 0-based index (points just past msgid)

    # Walk forward to the msgstr line.
    while i < total and not lines[i].startswith("msgstr"):
        i += 1

    # Collect the msgstr line and any quoted continuation lines.
    result: set[int] = set()
    while i < total:
        stripped = lines[i].strip()
        if stripped.startswith("msgstr") or stripped.startswith('"'):
            result.add(i + 1)  # convert back to 1-based
            i += 1
        else:
            break

    return result


def compute_counts(paths: list[str]) -> dict[str, int]:
    """Walk .po files and accumulate per-contributor translated-entry counts."""
    counts: dict[str, int] = defaultdict(int)

    for po_path in collect_files(paths):
        try:
            po = polib.pofile(str(po_path))
        except Exception as exc:
            print(f"  warning: could not parse {po_path}: {exc}")
            continue

        translated = [e for e in po.translated_entries() if "fuzzy" not in e.flags]
        if not translated:
            continue

        blame = git_blame_porcelain(po_path)
        if not blame:
            continue

        check_name_collisions(blame)

        for entry in translated:
            line_nums = msgstr_line_numbers(po_path, entry)
            if not line_nums:
                continue
            name = real_author_for_lines(blame, line_nums)
            if name:
                counts[NAME_ALIASES.get(name.lower(), name)] += 1
            else:
                counts["(unassigned)"] += 1

    return dict(counts)


def teammd_totals() -> dict[str, int]:
    """Return {username: translated count} from TEAM.md.

    TEAM.md is the canonical contributor record: the nightly run updates it
    from git blame for git-visible contributors and preserves the Transifex-era
    counts (including restored numbers) for everyone else, so this captures
    both eras.
    """
    path = REPO_ROOT / "TEAM.md"
    totals: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = TEAMMD_ROW_RE.match(line)
        if not m:
            continue
        t = int(m.group("t").split()[0])
        if t > 0:
            totals[m.group("user")] = t
    return totals


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(counts: dict[str, int]) -> None:
    total = sum(counts.values())
    print(f"Total non-fuzzy translated entries attributed: {total}\n")
    print("| Contributor | Translated |")
    print("|:------------|-----------:|")
    for user, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"| {user} | {count} |")


def update_teammd(counts: dict[str, int]) -> None:
    """Update the Translated column in TEAM.md; append rows for new handles.

    An existing count is never decreased: git-blame attribution only grows over
    time and can't observe Transifex-era work, so taking the max preserves
    restored numbers (e.g. Revisto's) across nightly runs.
    """
    path = REPO_ROOT / "TEAM.md"
    lines = path.read_text(encoding="utf-8").splitlines()

    # Build an index of existing rows by username.
    rows: dict[str, tuple[int, str, int]] = {}
    for i, line in enumerate(lines):
        m = TEAMMD_ROW_RE.match(line)
        if m:
            rows[m.group("user")] = (i, m.group("role"), int(m.group("t").split()[0]))

    touched: list[str] = []
    new_rows: list[str] = []

    for user, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        if user == "(unassigned)" or count <= 0:
            continue
        if user in rows:
            i, role, prev = rows[user]
            keep = max(prev, count)
            if keep == prev:
                continue
            lines[i] = f"| {user} | {role} | {keep} |"
            touched.append(user)
        else:
            role = NEW_ROW_ROLES.get(user, "translator")
            new_rows.append(f"| {user} | {role} | {count} |")
            touched.append(user)

    if new_rows:
        lines.extend(new_rows)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"TEAM.md: updated {len(touched)} contributor row(s)")
    if new_rows:
        print(f"  added {len(new_rows)} new row(s) — review roles manually")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Files or directories to scan (default: whole repo)",
    )
    parser.add_argument(
        "--update-teammd", action="store_true",
        help="rewrite TEAM.md from the computed counts",
    )
    args = parser.parse_args()

    counts = compute_counts(args.paths)

    if args.update_teammd:
        update_teammd(counts)
    else:
        print_report(counts)


if __name__ == "__main__":
    main()

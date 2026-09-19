#!/usr/bin/env python3
"""
scripts/po_sync.py

Shared logic for syncing this repo's .po files against a CPython docs
checkout. Used by both:

  - scripts/update_python_version.py  (manual, deliberate version bumps,
    full clone, creates new .po files for brand-new pages)
  - .github/workflows/sync-with-cpython.yml (nightly automated msgid sync,
    sparse checkout, merge-only by default, opens an issue for fuzzy strings)

Keeping this logic in one place means both paths build .pot files and run
msgmerge/msgfmt identically -- no more silent flag drift (e.g. one path
passing --no-location --no-wrap and the other not), which otherwise shows
up as spurious rewrap-only diffs on whichever path runs next.

Note: sphinx.po is built from TWO sources: CPython's Doc/ gettext
extraction (which includes template strings from indexcontent.html etc.)
AND Sphinx's own internal UI-string catalog (sphinx/locale/sphinx.pot).
sync_sphinx_catalog() combines both into one POT before merging, so
neither set of strings clobbers the other.

New upstream pages
------------------
When CPython adds a page (e.g. the whole Doc/library/builtins section), there
is a .pot for it but no .po in this repo. By default the sync only *reports*
these. Pass --create-new to sync-only (or create_new=True to merge_all) to
create an empty .po for each one via msginit, then normalise it with the same
msgmerge flags used everywhere else. New files have empty msgstrs, so the
English text shows until someone translates them.

This module is a library first, CLI second. As a CLI it exposes just the
"mechanical middle" of the sync -- build .pot templates, merge them into
existing .po files, create/flag new upstream pages with no .po yet, validate --
so the GitHub Actions workflow can shell out to one command instead of
reimplementing the loop in bash/awk.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Flags msgmerge is run with everywhere. Keeping this in one constant is the
# whole point: previously the script omitted --no-location --no-wrap while
# the workflow included them, so whichever ran second would produce a huge
# rewrap-only diff on top of (and obscuring) any real content changes.
MSGMERGE_FLAGS = ["--update", "--backup=off", "--no-location", "--no-wrap"]

DEFAULT_LOCALE = "fa"

IGNORED_DIR_NAMES = {".git", ".cpython-src", ".pot-templates"}


def run(
    cmd: list, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


def _is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIR_NAMES for part in path.parts)


def iter_po_files(repo_root: Path = REPO_ROOT):
    for po_path in sorted(repo_root.rglob("*.po")):
        if not _is_ignored(po_path.relative_to(repo_root)):
            yield po_path


# ---------------------------------------------------------------------------
# Fetch + build .pot templates
# ---------------------------------------------------------------------------


def fetch_cpython_full(tag: str, workdir: Path) -> None:
    """Full clone of CPython at `tag`. Used by the version-bump script,
    which needs the full doc tree to build every .pot (including ones for
    brand-new pages that a sparse checkout might not anticipate)."""
    if workdir.exists():
        shutil.rmtree(workdir)
    run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            tag,
            "https://github.com/python/cpython.git",
            str(workdir),
        ]
    )


def fetch_cpython_sparse(tag: str, workdir: Path) -> None:
    """Sparse, blobless clone of just Doc/ + Include/. Used by the nightly
    workflow. Doc/ contains everything needed to build every .pot, including
    ones for brand-new pages, so this is enough for --create-new too."""
    if workdir.exists():
        shutil.rmtree(workdir)
    run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            "--branch",
            tag,
            "https://github.com/python/cpython.git",
            str(workdir),
        ]
    )
    run(["git", "sparse-checkout", "set", "Doc", "Include"], cwd=workdir)


def build_gettext(doc_dir: Path) -> Path:
    """Build .pot templates from a CPython Doc/ checkout, return their root dir."""
    venv_dir = doc_dir / "venv"
    run([sys.executable, "-m", "venv", str(venv_dir)])
    pip = venv_dir / "bin" / "pip"
    sphinx_build = venv_dir / "bin" / "sphinx-build"
    run([str(pip), "install", "-r", "requirements.txt"], cwd=doc_dir)
    pot_root = doc_dir / "build" / "gettext"
    run([str(sphinx_build), "-b", "gettext", ".", str(pot_root)], cwd=doc_dir)
    return pot_root


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


@dataclass
class MergeReport:
    updated: list = field(default_factory=list)
    new_po_created: list = field(default_factory=list)
    missing_pot: list = field(
        default_factory=list
    )  # .po with no matching .pot upstream
    new_pot_no_po: list = field(
        default_factory=list
    )  # .pot with no .po yet (new upstream page), not created

    def summary(self) -> str:
        lines = [
            f"{len(self.updated)} .po files merged",
            f"{len(self.new_po_created)} new .po files created",
            f"{len(self.missing_pot)} .po files with no matching upstream source "
            f"(page may have been removed/renamed upstream)",
            f"{len(self.new_pot_no_po)} new upstream .pot files with no .po yet",
        ]
        return "Summary: " + ", ".join(lines)


def merge_existing(pot_root: Path, repo_root: Path = REPO_ROOT) -> MergeReport:
    report = MergeReport()
    for po_path in iter_po_files(repo_root):
        rel = po_path.relative_to(repo_root)
        # sphinx.po is handled by sync_sphinx_catalog, which does a single
        # combined merge of both the CPython-built pot and Sphinx's own
        # internal catalog -- so skip it here to avoid a double-merge that
        # clobbers the template strings sync_sphinx_catalog restores.
        if rel == Path("sphinx.po"):
            continue
        pot_path = pot_root / rel.with_suffix(".pot")
        if not pot_path.exists():
            print(
                f"  ! no matching .pot for {rel} "
                f"(page may have been removed/renamed upstream -- review manually)"
            )
            report.missing_pot.append(rel)
            continue
        run(["msgmerge", *MSGMERGE_FLAGS, str(po_path), str(pot_path)])
        report.updated.append(rel)
    return report


def detect_new_pot_files(pot_root: Path, repo_root: Path = REPO_ROOT) -> list:
    """Find .pot files with no corresponding .po file yet -- i.e. pages
    added upstream since the last sync. Returns paths relative to pot_root.

    sphinx.pot is excluded: sphinx.po is managed by sync_sphinx_catalog(),
    which needs Sphinx's own catalog too, so it must not be created from the
    CPython pot alone."""
    new_pot = []
    for pot_path in sorted(pot_root.rglob("*.pot")):
        rel = pot_path.relative_to(pot_root)
        if rel == Path("sphinx.pot"):
            continue
        po_path = repo_root / rel.with_suffix(".po")
        if not po_path.exists():
            new_pot.append(rel)
    return new_pot


def create_po_for_new_pot(
    pot_root: Path,
    rel_pot_paths: list,
    locale: str = DEFAULT_LOCALE,
    repo_root: Path = REPO_ROOT,
) -> list:
    """Create a fresh .po for each given new .pot (paths relative to pot_root).

    Uses msginit to get a proper header (Language, Plural-Forms, ...), then
    runs msgmerge with MSGMERGE_FLAGS so the new file is formatted exactly
    like every other file (no location comments, no wrapping) and doesn't
    produce a rewrap-only diff on the next sync. Existing .po files are never
    overwritten. Parent directories (e.g. a new library/builtins/ folder) are
    created as needed."""
    created = []
    for rel in rel_pot_paths:
        pot_path = pot_root / rel
        po_path = repo_root / rel.with_suffix(".po")
        if po_path.exists():
            print(f"  ! {rel.with_suffix('.po')} already exists, skipping")
            continue
        po_path.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "msginit",
                "--no-translator",
                "--no-wrap",
                "-l",
                locale,
                "-i",
                str(pot_path),
                "-o",
                str(po_path),
            ]
        )
        run(["msgmerge", *MSGMERGE_FLAGS, str(po_path), str(pot_path)])
        print(f"  + created {po_path.relative_to(repo_root)}")
        created.append(rel.with_suffix(".po"))
    return created


def merge_all(
    pot_root: Path,
    create_new: bool,
    locale: str = DEFAULT_LOCALE,
    repo_root: Path = REPO_ROOT,
) -> MergeReport:
    report = merge_existing(pot_root, repo_root)
    new_pot = detect_new_pot_files(pot_root, repo_root)
    if create_new:
        report.new_po_created = create_po_for_new_pot(
            pot_root, new_pot, locale, repo_root
        )
    else:
        report.new_pot_no_po = new_pot
    return report


# ---------------------------------------------------------------------------
# Sphinx's own UI-string catalog (separate from CPython's Doc/ content)
# ---------------------------------------------------------------------------


def find_sphinx_pot(venv_dir: Path) -> Path:
    """Locate sphinx.pot inside the sphinx version installed in `venv_dir`
    (the same venv build_gettext() creates from Doc/requirements.txt, so
    this stays pinned to whatever Sphinx version CPython's docs actually
    build with -- not whatever sphinx happens to be on the runner)."""
    python = venv_dir / "bin" / "python"
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import sphinx, os; print(os.path.dirname(sphinx.__file__))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    sphinx_dir = Path(result.stdout.strip())
    pot_path = sphinx_dir / "locale" / "sphinx.pot"
    if not pot_path.exists():
        raise FileNotFoundError(f"sphinx.pot not found at {pot_path}")
    return pot_path


def sync_sphinx_catalog(
    doc_venv_dir: Path,
    cpython_pot_root: Path | None = None,
    repo_root: Path = REPO_ROOT,
) -> bool:
    """Merge sphinx.po against a combined POT that includes both Sphinx's
    own internal UI strings and CPython's template strings (indexcontent.html
    etc). Without the combination, whichever POT runs second clobbers strings
    from the first, turning them into #~ orphans."""
    po_path = repo_root / "sphinx.po"
    if not po_path.exists():
        return False

    internal_pot = find_sphinx_pot(doc_venv_dir)
    cpython_pot = cpython_pot_root / "sphinx.pot" if cpython_pot_root else None

    if cpython_pot and cpython_pot.exists():
        # Combine both catalogs into one POT so a single msgmerge pass sees
        # everything. --use-first keeps CPython's template strings when a
        # msgid appears in both (they should be identical, but just in case).
        combined = doc_venv_dir / "sphinx_combined.pot"
        run(
            [
                "msgcat",
                "--use-first",
                str(cpython_pot),
                str(internal_pot),
                "-o",
                str(combined),
            ]
        )
        run(["msgmerge", *MSGMERGE_FLAGS, str(po_path), str(combined)])
        combined.unlink(missing_ok=True)
    else:
        # Fallback: no CPython-built sphinx.pot, just use Sphinx's internal one.
        run(["msgmerge", *MSGMERGE_FLAGS, str(po_path), str(internal_pot)])

    return True


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def check_po_files(repo_root: Path = REPO_ROOT) -> list:
    """Run msgfmt --check on every .po file. Returns a list of (path, stderr)
    for any that fail; empty list means all good."""
    bad = []
    for po_path in iter_po_files(repo_root):
        result = subprocess.run(
            ["msgfmt", "--check", "-o", "/dev/null", str(po_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            bad.append((po_path, result.stderr.strip()))
    return bad


# ---------------------------------------------------------------------------
# CLI -- the "sync-only" mode the workflow shells out to
# ---------------------------------------------------------------------------


def _cli_sync_only(args: argparse.Namespace) -> int:
    """Sparse clone + build gettext + merge into existing .po files +
    (optionally) create .po files for new upstream pages + validate.

    Without --create-new this is report-only for new upstream pages.
    With --create-new, empty .po files are created for them, so they show up
    in the commit and can be translated. --fail-on-new makes the run exit
    non-zero if new pages were found but not created (useful in CI to make
    sure they don't get ignored)."""
    workdir = REPO_ROOT / ".cpython-src"
    tag = args.tag
    doc_venv_dir = workdir / "Doc" / "venv"

    print(f"== Sparse-fetching CPython {tag} ==")
    fetch_cpython_sparse(tag, workdir)

    print("\n== Building gettext templates ==")
    pot_root = build_gettext(workdir / "Doc")

    print("\n== Merging into existing .po files ==")
    report = merge_all(pot_root, create_new=args.create_new, locale=args.locale)
    print(f"\n{report.summary()}")
    if report.new_po_created:
        print("\nCreated .po files for new upstream pages:")
        for rel in report.new_po_created:
            print(f"  + {rel}")
    if report.new_pot_no_po:
        print("\nNew upstream pages with no .po yet (re-run with --create-new):")
        for rel in report.new_pot_no_po:
            print(f"  - {rel}")

    print("\n== Syncing sphinx.po against installed Sphinx's own catalog ==")
    synced = sync_sphinx_catalog(doc_venv_dir, cpython_pot_root=pot_root)
    if synced:
        print("  sphinx.po merged against sphinx/locale/sphinx.pot")
    else:
        print("  no top-level sphinx.po found -- skipping")

    print("\n== Validating .po files ==")
    bad = check_po_files()
    if bad:
        print("\nBroken .po files (fix before committing):")
        for path, err in bad:
            print(f"  {path}:\n    {err}")

    if not args.keep_src:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(REPO_ROOT / ".pot-templates", ignore_errors=True)

    if bad:
        return 1
    if args.fail_on_new and report.new_pot_no_po:
        return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser(
        "sync-only",
        help="Sparse-checkout sync used by the nightly workflow: fetch, "
        "build gettext, merge into existing .po files, report (or create) "
        "new upstream pages, validate.",
    )
    sync.add_argument("tag", help="CPython git tag to sync against, e.g. v3.14.7")
    sync.add_argument(
        "--create-new",
        action="store_true",
        help="create empty .po files for upstream pages that have no .po yet "
        "(default: only report them)",
    )
    sync.add_argument(
        "--fail-on-new",
        action="store_true",
        help="exit with status 2 if new upstream pages have no .po and "
        "--create-new was not given",
    )
    sync.add_argument(
        "--locale",
        default=DEFAULT_LOCALE,
        help=f"locale for newly created .po files (default: {DEFAULT_LOCALE})",
    )
    sync.add_argument(
        "--keep-src",
        action="store_true",
        help="keep the scratch CPython checkout instead of deleting it",
    )
    sync.set_defaults(func=_cli_sync_only)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()

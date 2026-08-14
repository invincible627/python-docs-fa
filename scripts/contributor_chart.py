#!/usr/bin/env python3
"""Generate a top-N bar chart of translated counts and refresh README.

The chart uses the translated totals recorded in ``TEAM.md``
(``teammd_totals``), which the nightly maintenance run keeps in sync with
git-blame attribution while preserving Transifex-era and restored counts, and
draws a pastel bar chart saved to ``reports/contributor_stats_YYYY_MM_DD.png``.
Older charts are pruned and the README block between the
``STATS_START``/``STATS_END`` markers is refreshed.

Requires: ``pip install polib matplotlib``

Usage::

    python3 scripts/contributor_chart.py              # top 10 contributors
    python3 scripts/contributor_chart.py --top-n 15   # different cutoff
    python3 scripts/contributor_chart.py --dry-run    # report only, no writes
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

matplotlib.use("Agg")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from team_stats import teammd_totals  # noqa: E402

CHART_DIR = REPO_ROOT / "reports"
CHART_PREFIX = "contributor_stats_"
CHART_FILENAME = f"{CHART_PREFIX}%Y_%m_%d.png"

README_PATH = REPO_ROOT / "README.md"
STATS_START = "<!-- STATS_START -->"
STATS_END = "<!-- STATS_END -->"

PASTEL_COLORS = [
    "#A6C7E8",
    "#B5EAD7",
    "#FFDFD3",
    "#FFF1AC",
    "#E2D1F9",
    "#FFD7BA",
    "#FFABAB",
    "#C7F0DB",
    "#FFDAC1",
    "#C7CEEA",
]


def chart_data(counts: dict[str, int], top_n: int) -> list[tuple[str, int]]:
    """Return contributors with a positive count, sorted, trimmed to top_n."""
    eligible = [
        (name, count)
        for name, count in counts.items()
        if name != "(unassigned)" and count > 0
    ]
    return sorted(eligible, key=lambda item: item[1], reverse=True)[:top_n]


def draw_chart(data: list[tuple[str, int]], top_n: int) -> None:
    usernames = [name for name, _ in data]
    totals = [count for _, count in data]
    colors = [PASTEL_COLORS[i % len(PASTEL_COLORS)] for i in range(len(usernames))]

    plt.figure(figsize=(12, 7))
    bars = plt.bar(usernames, totals, color=colors)

    title = "User Contributions"
    if top_n:
        title += f" (Top {top_n})"
    plt.title(title)
    plt.xlabel("Username")
    plt.ylabel("Translated Count")
    plt.xticks(rotation=45, ha="right")

    plt.gca().yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    plt.grid(axis="y", linestyle="--", alpha=0.7)

    for bar in bars:
        height = bar.get_height()
        if height > 0:
            plt.text(
                bar.get_x() + bar.get_width() / 2.0,
                height + 0.1,
                f"{int(height)}",
                ha="center",
                fontweight="bold",
            )
    plt.tight_layout()


def prune_old_charts(keep: Path, dry_run: bool) -> list[Path]:
    removed: list[Path] = []
    for path in CHART_DIR.glob(f"{CHART_PREFIX}*.png"):
        if path == keep:
            continue
        if dry_run:
            print(f"[dry-run] would remove {path}")
        else:
            path.unlink()
        removed.append(path)
    return removed


def refresh_readme(chart_file: Path, dry_run: bool) -> bool:
    text = README_PATH.read_text(encoding="utf-8")
    date_iso = datetime.date.today().isoformat()
    rel = chart_file.relative_to(REPO_ROOT).as_posix()
    block = (
        f"{STATS_START}\n"
        "### مشارکت‌های کاربران\n"
        f"![مشارکت‌های کاربران]({rel})\n"
        f"(به‌روزرسانی: {date_iso})\n"
        f"{STATS_END}"
    )
    pattern = re.compile(
        re.escape(STATS_START) + ".*?" + re.escape(STATS_END),
        flags=re.DOTALL,
    )
    if not pattern.search(text):
        print(f"  error: could not find {STATS_START}/{STATS_END} in {README_PATH}")
        return False
    new_text = pattern.sub(block, text)
    if new_text == text:
        print("  README block unchanged")
        return False
    if dry_run:
        print("[dry-run] would update README stats block")
    else:
        README_PATH.write_text(new_text, encoding="utf-8")
        print(f"Updated README stats block to {rel} ({date_iso})")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="limit the chart to the top N contributors (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be done without changing files",
    )
    args = parser.parse_args()

    counts = teammd_totals()
    data = chart_data(counts, args.top_n)
    if not data:
        print("No contributor data to generate chart.")
        return

    today = datetime.date.today().strftime(CHART_FILENAME)
    out_path = CHART_DIR / today

    draw_chart(data, args.top_n)
    if args.dry_run:
        print(f"[dry-run] would save chart to {out_path}")
    else:
        CHART_DIR.mkdir(parents=True, exist_ok=True)
        plt.savefig(out_path)
        print(f"Saved user contributions chart to {out_path}")
    plt.close()

    prune_old_charts(out_path, args.dry_run)
    refresh_readme(out_path, args.dry_run)


if __name__ == "__main__":
    main()

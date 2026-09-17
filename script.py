# #!/usr/bin/env python3
# """
# Remove the `#, fuzzy` marker that precedes the header entry
# (msgid "" / msgstr "") in .po files.

# Usage:
#     python remove_fuzzy_header.py path/to/dir_or_files...
# """
# import sys
# import re
# from pathlib import Path

# # Matches "#, fuzzy\n" immediately followed by the empty msgid/msgstr header
# PATTERN = re.compile(
#     r'^#, fuzzy\n(?=msgid ""\nmsgstr "")',
#     re.MULTILINE
# )

# def process_file(path: Path) -> bool:
#     text = path.read_text(encoding="utf-8")
#     new_text, count = PATTERN.subn("", text)
#     if count:
#         path.write_text(new_text, encoding="utf-8")
#         print(f"Updated: {path} ({count} removal)")
#         return True
#     return False

# def main(paths):
#     files = []
#     for p in paths:
#         p = Path(p)
#         if p.is_dir():
#             files.extend(p.rglob("*.po"))
#         elif p.suffix == ".po":
#             files.append(p)

#     if not files:
#         print("No .po files found.")
#         return

#     changed = 0
#     for f in files:
#         if process_file(f):
#             changed += 1

#     print(f"\nDone. {changed}/{len(files)} file(s) modified.")

# if __name__ == "__main__":
#     if len(sys.argv) < 2:
#         print("Usage: python remove_fuzzy_header.py <dir_or_files...>")
#         sys.exit(1)
#     main(sys.argv[1:])

#!/usr/bin/env python3
"""
Fix .po headers where the Last-Translator line is missing its trailing
\\n before the closing quote, causing it to swallow the next header
line (e.g. Language-Team) into the same string.

Broken:
    "Last-Translator: NAME <EMAIL>, YEAR"
    "Language-Team: ...\\n"

Fixed:
    "Last-Translator: NAME <EMAIL>, YEAR\\n"
    "Language-Team: ...\\n"

Usage:
    python fix_last_translator_newline.py --dry-run path/to/dir
    python fix_last_translator_newline.py path/to/dir
"""
import re
import argparse
from pathlib import Path

# Matches a Last-Translator line whose value ends directly in a
# closing quote with NO \n escape before it.
PATTERN = re.compile(
    r'^"Last-Translator: ([^"\n]*?)"\n(?!")',  # lookahead not required but harmless
    re.MULTILINE,
)
# More precise: only match if it does NOT already end with \n before the quote
BROKEN = re.compile(r'^"Last-Translator: ([^"\n]*[^\\n])"\n', re.MULTILINE)


def fix_text(text: str):
    def repl(m):
        value = m.group(1)
        return f'"Last-Translator: {value}\\n"\n'

    new_text, count = BROKEN.subn(repl, text)
    return new_text, count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = []
    for p in args.paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.po")))
        elif p.suffix == ".po":
            files.append(p)

    changed = 0
    for f in files:
        if ".git" in f.parts:
            continue
        text = f.read_text(encoding="utf-8")
        new_text, count = fix_text(text)
        if count:
            print(f"{'Would fix' if args.dry_run else 'Fixed'}: {f} ({count} line(s))")
            if not args.dry_run:
                f.write_text(new_text, encoding="utf-8")
            changed += 1

    print(f"\nDone. {changed} file(s) {'would be' if args.dry_run else 'were'} fixed.")


if __name__ == "__main__":
    main()

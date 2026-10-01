#!/usr/bin/env python3
"""Keep the "pending review" section of the master issue in sync.

Lists every open issue carrying the sync label (created by the
"Sync with CPython" workflow) and rewrites that section of the master issue.
If an issue has an open linked PR, "درخواست ادغام: #<PR>" is added next to it.

Only the text between the two HTML-comment markers is ever touched. On the
first run the markers are inserted right below the section heading.

Env: GH_TOKEN, GITHUB_REPOSITORY (set by Actions); optional MASTER_ISSUE,
REVIEW_LABEL.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

REPO = os.environ["GITHUB_REPOSITORY"]
MASTER = os.environ.get("MASTER_ISSUE", "83")
LABEL = os.environ.get("REVIEW_LABEL", "تغییرات خودکار در ترجمه‌ها")
HEADING = "### تغییرات خودکارِ در انتظار بررسی"
START = "<!-- auto-review:start -->"
END = "<!-- auto-review:end -->"

QUERY = """
query($owner: String!, $name: String!, $label: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, states: OPEN, labels: [$label],
           orderBy: {field: CREATED_AT, direction: ASC}) {
      nodes {
        number
        closedByPullRequestsReferences(first: 5, includeClosedPrs: false) {
          nodes { number state }
        }
      }
    }
  }
}
"""


def gh(*args):
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


def fetch_items():
    owner, name = REPO.split("/", 1)
    out = gh(
        "api", "graphql",
        "-f", f"query={QUERY}",
        "-F", f"owner={owner}", "-F", f"name={name}", "-f", f"label={LABEL}",
    )
    nodes = json.loads(out)["data"]["repository"]["issues"]["nodes"]
    items = []
    for n in nodes:
        if str(n["number"]) == str(MASTER):
            continue
        prs = [
            p["number"]
            for p in n["closedByPullRequestsReferences"]["nodes"]
            if p["state"] == "OPEN"
        ]
        items.append((n["number"], prs))
    return items


def render(items):
    if not items:
        return "_موردی در انتظار بررسی نیست._"
    lines = []
    for number, prs in items:
        line = f"- #{number}"
        if prs:
            line += " — درخواست ادغام: " + "، ".join(f"#{p}" for p in prs)
        lines.append(line)
    return "\n".join(lines)


def splice(body, block):
    body = body.replace("\r\n", "\n")
    section = f"{START}\n{block}\n{END}"
    if START in body and END in body:
        pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
        return pattern.sub(lambda _: section, body, count=1)

    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == HEADING:
            lines[i + 1:i + 1] = ["", section]
            return "\n".join(lines)
    raise SystemExit(f"Heading not found in issue #{MASTER}: {HEADING}")


def main():
    body = gh("issue", "view", str(MASTER), "--repo", REPO,
              "--json", "body", "-q", ".body")
    old = body.replace("\r\n", "\n").rstrip("\n")
    new = splice(body, render(fetch_items())).rstrip("\n")

    if new == old:
        print("Master issue already up to date.")
        return
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as f:
        f.write(new + "\n")
    gh("issue", "edit", str(MASTER), "--repo", REPO, "--body-file", f.name)
    print(f"Updated issue #{MASTER}.")


if __name__ == "__main__":
    sys.exit(main())
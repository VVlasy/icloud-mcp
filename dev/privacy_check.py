"""Refuse private details in the repository: home-network addresses, personal home-folder paths and secret formats.

    python dev/privacy_check.py                 # every tracked file
    python dev/privacy_check.py origin/main     # also every line added by each commit since that ref, and its author

The commit scan matters because a value added in one commit and removed in the next is still published with the history.
Commit authors must be no-reply addresses: GitHub's, or the one Claude Code signs its commits with (noreply@anthropic.com);
a personal address in the author field would be published with every clone.
Precise map coordinates (5+ decimals) are refused too. A line containing `privacy-ok` is skipped: use it for deliberately generic test values (an address from a private range
that a test needs, for example). Placeholders such as `/Users/YOU/` are allowed as they are.
"""
from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = [
    ("private network address",
     re.compile(r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])")),
    # latitude,longitude with 5+ decimals points at a single building; examples use a public landmark at 4 decimals
    ("precise map coordinates", re.compile(r"(?<![\d.])-?\d{1,3}\.\d{5,}\s*,\s*-?\d{1,3}\.\d{5,}(?!\d)")),
    ("home-folder path", re.compile(r"/Users/(?!YOU\b|you\b|Shared\b|<)[A-Za-z][\w.-]*|/home/(?!runner\b|user\b|<)[a-z][\w.-]*")),
    ("secret", re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
                          r"|xox[bap]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY")),
]
NOREPLY = re.compile(r"(?:^|@)users\.noreply\.github\.com$|^noreply@github\.com$|^noreply@anthropic\.com$")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def scan(where: str, text: str) -> list[str]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if "privacy-ok" in line:
            continue
        for what, rx in PATTERNS:
            if rx.search(line):
                found.append(f"{where}:{n}: {what}")   # never the value itself: CI logs of a public repo are public
    return found


def main(base: str | None) -> int:
    problems: list[str] = []
    for path in git("ls-files").splitlines():
        try:
            data = open(path, "rb").read()
        except OSError:
            continue
        if b"\0" not in data[:4096]:
            problems += scan(path, data.decode("utf-8", "replace"))
    if base:
        for sha in git("rev-list", f"{base}..HEAD").split():
            email = git("show", "-s", "--format=%ae", sha).strip()
            if not NOREPLY.search(email.lower()):
                problems.append(f"commit {sha[:8]}: author email is not a GitHub noreply address")
            current = "?"
            for line in git("show", "--format=", "--unified=0", "--no-color", sha).splitlines():
                if line.startswith("+++ "):
                    current = line[6:] if line.startswith("+++ b/") else line[4:]
                elif line.startswith("+") and not line.startswith("+++"):
                    problems += [p.replace("<added>:1", f"commit {sha[:8]} {current}") for p in scan("<added>", line[1:])]
            problems += [p.replace("<message>", f"commit {sha[:8]} message") for p in scan("<message>", git("show", "-s", "--format=%B", sha))]
    for p in problems:
        print(p)
    print(f"privacy check: {len(problems)} problem(s)" if problems else "privacy check: clean")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))

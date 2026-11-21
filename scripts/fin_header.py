#!/usr/bin/env python3
"""Apply the Fin project header to a repository's source files.

Written once for the whole ecosystem so ten repositories do not grow ten headers
that differ by a typo. Idempotent: re-running rewrites nothing.

    python3 fin_header.py --repo fin-kv --license MIT src tests
    python3 fin_header.py --repo fin-kv --license MIT --check .

WHY A SCRIPT RATHER THAN A SED LOOP
------------------------------------
  * the comment spelling differs (// for Fin/C++/Rust/JS/TS, # for Python/shell/TOML);
  * a shebang must stay the first line or the script stops being executable;
  * an existing header must be replaced, not duplicated -- two SPDX lines is a defect;
  * the ones that are already identical must not be touched at all, so `git diff`
    shows only real changes.
"""

import argparse
import re
import sys
from pathlib import Path

REPO_URL = "https://github.com/M1778/{repo}"
PROFILE_URL = "https://github.com/M1778M/"
COPYRIGHT_YEAR = "2027"

# The brand block is the same in every Fin project; only the repository line and the
# licensing paragraph change.
BRAND = [
    "Fin Programming Language",
    "",
    "Made with \u2764\ufe0f for someone special.",
    "",
    "This project is genuinely built on love and dedication, a way to express your love can be done through Fin.",
    "Fin is a semi-object-orientated programming language that specialized in control, giving so much control to the user while asking for the bare minimum.",
    "Fin is the name of the Angel from BlackNightmare-Stories not yet published.",
    "\u201cLove is the reason this project exists\u201d",
    "-" * 77,
    "Author: M1778",
]

LICENSE_TEXT = {
    "MIT": [
        "Licensed under the MIT License. See LICENSE in the repository root.",
    ],
    "GPL-3.0-or-later": [
        "This program is free software: you can redistribute it and/or modify",
        "it under the terms of the GNU General Public License as published by",
        "the Free Software Foundation, either version 3 of the License, or",
        "(at your option) any later version.",
        "",
        "This program is distributed in the hope that it will be useful,",
        "but WITHOUT ANY WARRANTY; without even the implied warranty of",
        "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the",
        "GNU General Public License for more details.",
    ],
}

HASH_COMMENT = {".py", ".sh", ".bash", ".zsh", ".toml", ".yml", ".yaml", ".ini", ".cfg"}
SLASH_COMMENT = {
    ".fin", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".c", ".rs", ".js", ".mjs",
    ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".swift", ".go", ".zig", ".m",
}

SKIP_DIRS = {".git", "target", "node_modules", "build", "dist", ".finn", "vendor"}
SKIP_NAMES = {
    "LICENSE", "COPYING", "NOTICE", "CMakeUserPresets.json", "package-lock.json",
    "Cargo.lock", "poetry.lock", "uv.lock", "pnpm-lock.yaml", "yarn.lock",
}
SPDX_RE = re.compile(r"^[#/\s\-\*]*SPDX-License-Identifier:", re.MULTILINE)


def comment_prefix(path: Path) -> str | None:
    if path.suffix in HASH_COMMENT:
        return "#"
    if path.suffix in SLASH_COMMENT:
        return "//"
    return None


def strip_old_header(text: str, marker: str) -> str:
    """Remove a header this script (or the Fin one) previously wrote.

    The header starts at the banner line and ends at the closing banner. Anything
    before the start is preserved -- that is where a shebang lives.
    """
    start = text.find(marker + " " + "=" * 77)
    if start < 0:
        return text
    end = text.find(marker + " " + "=" * 77, start + 1)
    if end < 0:
        return text
    # Only treat it as ours when the block really is a header (contains the brand or
    # an SPDX line), so a comment banner in prose is left alone.
    block = text[start:end]
    if "Fin Programming Language" not in block and "SPDX-License-Identifier" not in block:
        return text
    end = text.find("\n", end)
    if end < 0:
        return text
    after = text[end + 1 :]
    # Drop the blank line the header is separated from the code by.
    if after.startswith("\n"):
        after = after[1:]
    return text[:start] + after


def build_header(repo: str, license_id: str, prefix: str) -> str:
    lines = [prefix + " " + "=" * 77]
    for entry in BRAND:
        lines.append((prefix + " " + entry).rstrip())
    lines.append(prefix + " Repository: " + REPO_URL.format(repo=repo))
    lines.append(prefix + " Profile: " + PROFILE_URL)
    lines.append(prefix + " " + "-" * 77)
    lines.append(prefix + " Copyright (C) " + COPYRIGHT_YEAR + " M1778")
    lines.append(prefix)
    lines.append(
        prefix + " This file is part of " + repo + ", a project for the Fin Programming Language."
    )
    lines.append(prefix)
    for entry in LICENSE_TEXT[license_id]:
        lines.append((prefix + " " + entry).rstrip())
    lines.append(prefix)
    lines.append(prefix + " SPDX-License-Identifier: " + license_id)
    lines.append(prefix + " " + "=" * 77)
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--license", default="MIT", choices=sorted(LICENSE_TEXT))
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()

    changed, checked = [], 0
    for root in args.paths:
        base = Path(root)
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in files:
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.name in SKIP_NAMES:
                continue
            prefix = comment_prefix(path)
            if prefix is None:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            checked += 1
            header = build_header(args.repo, args.license, prefix)
            body = strip_old_header(text, prefix)
            # Keep a shebang (and an encoding cookie) above the header.
            lead = ""
            for line in body.splitlines(keepends=True)[:2]:
                if line.startswith("#!") or "coding:" in line:
                    lead += line
            if lead:
                body = body[len(lead) :]
            new = lead + header + ("\n" if body and not body.startswith("\n") else "") + body
            if new != text:
                changed.append(str(path))
                if not args.check:
                    path.write_text(new, encoding="utf-8")

    verb = "would change" if args.check else "changed"
    print(f"{verb} {len(changed)} of {checked} source files")
    for name in changed:
        print("  " + name)
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

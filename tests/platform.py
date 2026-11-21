# =============================================================================
# Fin Programming Language
#
# Made with ❤️ for someone special.
#
# This project is genuinely built on love and dedication, a way to express your love can be done through Fin.
# Fin is a semi-object-orientated programming language that specialized in control, giving so much control to the user while asking for the bare minimum.
# Fin is the name of the Angel from BlackNightmare-Stories not yet published.
# “Love is the reason this project exists”
# -----------------------------------------------------------------------------
# Author: M1778
# Repository: https://github.com/M1778/fin-kv
# Profile: https://github.com/M1778M/
# -----------------------------------------------------------------------------
# Copyright (C) 2027 M1778
#
# This file is part of fin-kv, a project for the Fin Programming Language.
#
# Licensed under the MIT License. See LICENSE in the repository root.
#
# SPDX-License-Identifier: MIT
# =============================================================================

"""Platform acceptance: the binary must either serve, or refuse by name.

fin-kv's store needs Linux `uname`, `flock`, and the per-architecture
`O_DIRECTORY`/`O_NOFOLLOW` bits, so `src/main.fin` selects a whole-file branch
with `#c_ifdef FIN_TARGET_LINUX`. Off Linux that branch is a program whose only
job is to name the requirement and exit 1 -- not an empty success, which would
let a Windows or macOS lane go green without testing anything.

The expected outcome is chosen by the host, and can be forced:

    python3 tests/platform.py --bin /path/to/fin-kv                # by host
    python3 tests/platform.py --bin ./refusal-build --expect refusal

`--expect refusal` exists so the refusal branch is exercised on Linux too,
where it is otherwise unreachable. It is not a way to make a failing lane pass:
the assertion still requires the named message and a nonzero exit.
"""

import argparse
import tempfile
from pathlib import Path

from kv import Server, expect, resolve_binary

REFUSAL = "fin-kv requires Linux"


def serves(binary, root):
    with Server(binary, root) as server:
        with server.client() as c:
            expect(c.cmd("PING"), "PONG")
            expect(c.cmd("SET platform ok"), "OK")
            expect(c.cmd("GET platform"), "ok")
    assert (Path(root) / "fin-kv.aof").exists(), "the server did not create its AOF"
    print("ok [server starts, answers, and persists]", flush=True)


def refuses(binary, root):
    server = Server(binary, root, ready=False)
    try:
        output, _ = server.p.communicate(timeout=30)
    finally:
        if server.p.poll() is None:
            server.stop(kill=True)
    text = output.decode(errors="replace")
    assert server.p.returncode != 0, f"refusal exited 0: {text!r}"
    assert REFUSAL in text, f"refusal did not name the platform: {text!r}"
    assert not (Path(root) / "fin-kv.aof").exists(), "refusal created an AOF"
    print(f"ok [named refusal, exit {server.p.returncode}]", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("--expect", choices=("auto", "server", "refusal"), default="auto")
    args = parser.parse_args()
    binary = resolve_binary(args.bin)
    expected = args.expect
    if expected == "auto":
        expected = "server" if __import__("sys").platform.startswith("linux") else "refusal"
    with tempfile.TemporaryDirectory(prefix="finkv-platform-") as temporary:
        (serves if expected == "server" else refuses)(binary, temporary)
    print("PLATFORM OK", flush=True)


if __name__ == "__main__":
    main()

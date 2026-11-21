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

"""Build and run every test the host can execute. One command for CI and humans.

    python3 tests/run.py                       # build with `finc` on PATH
    python3 tests/run.py --compiler build/finc --opt O2
    python3 tests/run.py --bin out/fin-kv      # test an existing binary

On Linux the full suites run: platform acceptance, protocol boundaries, and the
durability/recovery suite. Off Linux only platform acceptance is meaningful,
because `src/main.fin` compiles to the named refusal there; this script says so
out loud instead of printing a pass for work it did not do.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from kv import ROOT, resolve_binary

TESTS = Path(__file__).resolve().parent


def build(compiler, opt, destination, fin_libs):
    command = [compiler, str(ROOT / "src" / "main.fin"), "-o", str(destination), f"-{opt}"]
    if fin_libs:
        command += ["--fin-libs", fin_libs]
    subprocess.run(command, check=True, timeout=300)


def run(script, binary, *extra):
    command = [sys.executable, str(TESTS / script), "--bin", str(binary), *extra]
    subprocess.run(command, check=True, timeout=1800, cwd=TESTS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default=os.environ.get("FINC", "finc"))
    parser.add_argument("--fin-libs", default=os.environ.get("FIN_LIBS"))
    parser.add_argument("--opt", choices=("O0", "O2"), default="O0")
    parser.add_argument("--bin", type=Path, help="test an existing binary instead of building")
    parser.add_argument("--store", choices=("auto", "server", "refusal"), default="auto",
                        help="what the binary is expected to do; default from the host")
    args = parser.parse_args()

    expected = args.store
    if expected == "auto":
        expected = "server" if sys.platform.startswith("linux") else "refusal"

    with tempfile.TemporaryDirectory(prefix="finkv-run-") as temporary:
        if args.bin:
            binary = resolve_binary(args.bin)
        else:
            binary = Path(temporary) / "fin-kv"
            build(args.compiler, args.opt, binary, args.fin_libs)
            binary = resolve_binary(binary)
            print(f"built {binary} (-{args.opt})", flush=True)
        if expected == "server":
            run("platform.py", binary)
            run("boundary.py", binary)
            run("smoke.py", binary)
        else:
            print(f"{sys.platform}: store is Linux-only; running the named-refusal check only",
                  flush=True)
            run("platform.py", binary, "--expect", "refusal")
    print("ALL TESTS OK", flush=True)


if __name__ == "__main__":
    main()

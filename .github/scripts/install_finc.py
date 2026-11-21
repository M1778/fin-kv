#!/usr/bin/env python3
"""Install a pinned finc release without building the compiler from source.

WHY THIS IS A SCRIPT AND NOT INLINE YAML
----------------------------------------
Ten ecosystem repositories need the compiler in CI. Copying the same forty lines of shell
into ten workflows is how they drift: one gets the sha256 check, nine do not; one pins a
version, nine take `latest` and go red the day a release breaks.

This is the one implementation. It reads FINC_VERSION from the repository root, downloads
that release's index, verifies the archive's sha256 against it, unpacks `bin/finc` and
`lib/std`, and refuses to continue if the binary cannot answer `--version`.

    python3 .github/scripts/install_finc.py
    python3 .github/scripts/install_finc.py --dest ~/.finc --version 1.0.1

It works on Linux, macOS and Windows: it reads the host triple from the running Python
(platform.machine + sys.platform), not from `uname`, and it unpacks with tarfile/zipfile
rather than shelling out, so a Windows runner needs no GNU tar.

The musl archive is preferred on Linux: it is statically linked, so it needs no LLVM
runtime on the runner and cannot fail for a reason unrelated to the repository being
tested. That is the same reason the docs recommend it to users.
"""

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "M1778/Fin"


def host_triples() -> list[str]:
    """Candidate release triples for this machine, best first.

    Two entries at most: the plain one and, on Linux, the static musl build, which is the
    one that carries no LLVM runtime dependency.
    """
    machine = platform.machine().lower()
    arch = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(machine)
    if arch is None:
        print(f"error: unsupported machine {machine!r}", file=sys.stderr)
        sys.exit(1)

    if sys.platform.startswith("linux"):
        return [f"{arch}-unknown-linux-musl", f"{arch}-unknown-linux-gnu"]
    if sys.platform == "darwin":
        return [f"{arch}-apple-darwin"]
    if sys.platform == "win32":
        return [f"{arch}-pc-windows-msvc"]
    print(f"error: unsupported platform {sys.platform!r}", file=sys.stderr)
    sys.exit(1)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fin-ci-installer"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def unpack(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="defaults to the FINC_VERSION file")
    ap.add_argument("--dest", type=Path, default=None, help="defaults to ./.finc")
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    args = ap.parse_args()

    version = args.version
    if version is None:
        pin = args.repo_root / "FINC_VERSION"
        if not pin.exists():
            print(f"error: {pin} does not exist and --version was not given", file=sys.stderr)
            return 1
        version = pin.read_text(encoding="utf-8").strip()
    version = version.lstrip("v")

    dest = (args.dest or args.repo_root / ".finc").resolve()
    binary_name = "finc.exe" if sys.platform == "win32" else "finc"

    if (dest / "bin" / binary_name).exists():
        # Already installed by this script in an earlier job step; confirm it runs rather
        # than reinstalling, so a broken cached tree is still caught.
        out = subprocess.run(
            [str(dest / "bin" / binary_name), "--version"],
            capture_output=True, text=True,
        )
        if out.returncode == 0:
            print(f"finc already installed: {out.stdout.strip()}")
            print(f"::add-path::{dest / 'bin'}")
            return 0
        print("cached finc does not answer --version; reinstalling", file=sys.stderr)

    index_url = f"https://github.com/{REPO}/releases/download/v{version}/index.json"
    print(f"reading {index_url}")
    index = json.loads(fetch(index_url))

    entry = index.get("versions", {}).get(version)
    if entry is None:
        known = ", ".join(sorted(index.get("versions", {}))) or "none"
        print(f"error: finc {version} is not in the index. Published: {known}", file=sys.stderr)
        return 1

    targets = entry.get("targets", {})
    triple = None
    for candidate in host_triples():
        if candidate in targets:
            triple = candidate
            break
    if triple is None:
        print(
            f"error: finc {version} publishes no build for "
            f"{' or '.join(host_triples())}. Available: {', '.join(sorted(targets))}",
            file=sys.stderr,
        )
        return 1

    target = targets[triple]
    expected = (target.get("sha256") or "").strip().lower()
    if not expected:
        print(
            f"error: the index gives no sha256 for finc {version} on {triple}; "
            "refusing to install an unverifiable compiler",
            file=sys.stderr,
        )
        return 1

    url = target["url"]
    print(f"downloading {url}")
    payload = fetch(url)
    got = hashlib.sha256(payload).hexdigest()
    if got != expected:
        print(
            f"error: checksum mismatch for {url}\n  index:    {expected}\n  download: {got}",
            file=sys.stderr,
        )
        return 1
    print(f"sha256 ok ({len(payload)} bytes)")

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / (target.get("file") or "finc-archive")
        archive.write_bytes(payload)
        staging = Path(tmp) / "unpacked"
        staging.mkdir()
        unpack(archive, staging)

        if not (staging / "bin" / binary_name).exists():
            print(
                f"error: the archive has no bin/{binary_name}; its layout is not what this "
                "installer expects",
                file=sys.stderr,
            )
            return 1
        if not (staging / "lib" / "std").is_dir():
            print(
                "error: the archive has no lib/std; refusing a compiler with no standard "
                "library",
                file=sys.stderr,
            )
            return 1

        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(dest))

    binary = dest / "bin" / binary_name
    if not binary.exists():
        print(f"error: {binary} is missing after unpacking", file=sys.stderr)
        return 1
    if sys.platform != "win32":
        binary.chmod(0o755)

    out = subprocess.run([str(binary), "--version"], capture_output=True, text=True)
    if out.returncode != 0:
        print(
            f"error: the installed finc does not answer --version (exit {out.returncode}).\n"
            f"stdout: {out.stdout.strip()}\nstderr: {out.stderr.strip()}\n"
            "That release's artifact for this platform is not usable; pick another version "
            "in FINC_VERSION.",
            file=sys.stderr,
        )
        shutil.rmtree(dest, ignore_errors=True)
        return 1

    line = out.stdout.strip()
    print(f"installed: {line}")
    if "(contract " not in line:
        print(
            "error: --version did not report a contract integer, so this is not a finc this "
            "tooling can drive",
            file=sys.stderr,
        )
        return 1

    # GitHub Actions picks this up; harmless elsewhere.
    print(f"::add-path::{dest / 'bin'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

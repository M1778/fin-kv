#!/usr/bin/env python3
"""Install the LLVM runtime a released finc artifact needs, when it needs one.

WHY THIS EXISTS
---------------
The published `finc` artifacts are not uniform in what they need at run time, and the
difference is invisible until the binary runs:

    x86_64-unknown-linux-musl     static-pie, 93 MB, needs nothing
    *-pc-windows-msvc             LLVM linked in, needs nothing
    *-unknown-linux-gnu           needs libLLVM.so.22.1
    *-apple-darwin                needs /opt/homebrew/opt/llvm@22/lib/libLLVM.dylib
                                  by ABSOLUTE PATH, so Homebrew must be at that exact prefix

On a runner without it the loader aborts before `main`:

    dyld[1186]: Library not loaded: /opt/homebrew/opt/llvm@22/lib/libLLVM.dylib
      Reason: tried: '/opt/homebrew/opt/llvm@22/lib/libLLVM.dylib' (no such file)

Then every downstream step fails on a compiler that looks installed. Finding this costs an
hour per person who hits it, which is why it is a script instead of a paragraph.

    python3 .github/scripts/install_finc_runtime.py

It is idempotent, needs no arguments, and does nothing on a platform whose artifact does not
need a runtime. `INSTALL.md` in the compiler repository carries the same table; keeping the
two in step is the maintenance cost of not having this failure.
"""

import os
import shutil
import subprocess
import sys


def have(command: str) -> bool:
    return shutil.which(command) is not None


def run(*args: str) -> int:
    print("+", " ".join(args))
    return subprocess.call(args)


def macos() -> int:
    """The darwin artifacts name an absolute Homebrew path, so the keg must be there."""
    prefix = "/opt/homebrew" if os.uname().machine == "arm64" else "/usr/local"
    keg = f"{prefix}/opt/llvm@22"
    if os.path.isdir(keg):
        print(f"llvm@22 already present at {keg}")
        return 0
    if not have("brew"):
        print(
            f"error: {keg} is missing and Homebrew is not installed, so the pinned LLVM "
            "runtime cannot be provided.\n"
            "The macOS release artifacts link it by absolute path; there is no other way to "
            "satisfy them. Use a source build (`finn download --source`) or the musl archive "
            "on Linux.",
            file=sys.stderr,
        )
        return 1
    # A keg-only formula: `brew install llvm@22` is never linked into the prefix, which is
    # fine -- the artifact names the keg path itself, so linking would change nothing.
    status = run("brew", "install", "llvm@22")
    if status != 0:
        print("error: `brew install llvm@22` failed", file=sys.stderr)
        return status
    if not os.path.isdir(keg):
        print(f"error: brew reported success but {keg} does not exist", file=sys.stderr)
        return 1
    print(f"llvm@22 installed at {keg}")
    return 0


def linux() -> int:
    """The glibc artifacts need libLLVM.so.22; the musl one needs nothing."""
    # `ldconfig -p` is the honest test: it asks the loader what it can actually find, which
    # is the same question the linker asks at startup. A file on disk that no search path
    # covers would not help.
    try:
        listing = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True, check=False
        ).stdout
    except FileNotFoundError:
        listing = ""
    if "libLLVM.so.22" in listing:
        print("libLLVM.so.22 is already resolvable")
        return 0

    if not have("apt-get"):
        print(
            "error: libLLVM.so.22 is not resolvable and this is not a Debian-family system, "
            "so this script cannot install it.\n"
            "Install the LLVM 22 runtime package for your distribution, or use the static "
            "musl archive, which needs no runtime at all.",
            file=sys.stderr,
        )
        return 1

    # apt.llvm.org, because a distribution's own LLVM is usually not the pinned major.
    key = "/usr/share/keyrings/llvm-archive-keyring.gpg"
    if not os.path.exists(key):
        if run("sudo", "apt-get", "update", "-qq") != 0:
            return 1
        if run("sudo", "apt-get", "install", "-y", "-qq", "gpg", "curl") != 0:
            return 1
        if run(
            "bash", "-c",
            f"curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | "
            f"sudo gpg --dearmor -o {key}",
        ) != 0:
            return 1

    with open("/etc/os-release", encoding="utf-8") as fh:
        codename = ""
        for line in fh:
            if line.startswith("VERSION_CODENAME="):
                codename = line.split("=", 1)[1].strip().strip('"')
                break
    if not codename:
        print("error: could not read VERSION_CODENAME from /etc/os-release", file=sys.stderr)
        return 1

    source = f"/etc/apt/sources.list.d/llvm22.list"
    if not os.path.exists(source):
        entry = (
            f"deb [signed-by={key}] https://apt.llvm.org/{codename}/ "
            f"llvm-toolchain-{codename}-22 main\n"
        )
        if run("bash", "-c", f"printf '%s' '{entry}' | sudo tee {source} > /dev/null") != 0:
            return 1
    if run("sudo", "apt-get", "update", "-qq") != 0:
        return 1
    if run("sudo", "apt-get", "install", "-y", "-qq", "libllvm22") != 0:
        return 1
    print("libllvm22 installed")
    return 0


def main() -> int:
    if sys.platform == "darwin":
        return macos()
    if sys.platform.startswith("linux"):
        return linux()
    # Windows links LLVM into the binary; nothing to do.
    print(f"{sys.platform}: the released artifact needs no external LLVM runtime")
    return 0


if __name__ == "__main__":
    sys.exit(main())

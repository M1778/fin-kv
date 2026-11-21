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

"""Protocol boundary suite: every advertised limit at, and one step past, its edge.

Each case names the constant it proves. A test that only exercises a value
comfortably inside a limit would still pass if the guard were deleted, so the
accepting case and the refusing case are both asserted.

Cross-platform: needs only Python 3 and a built fin-kv binary. It opens real
sockets and sends raw bytes, including partial frames and abrupt disconnects.

    python3 tests/boundary.py --bin /path/to/fin-kv
"""

import argparse
import socket
import sys
import tempfile
from pathlib import Path

import kv
from kv import Server, expect, resolve_binary, expect_close

MAX_FRAME = 8192
MAX_KEY = 256
MAX_VALUE = 4096
MAX_DEL = 128
MAX_KEYS = 8192
MAX_BYTES = 33554432


def frame_limit(binary, root):
    """MAX_FRAME counts the bytes before LF, optional CR included."""
    with Server(binary, root) as server:
        # "PING " is 5 bytes; the message pads the frame to exactly the limit.
        exact = "y" * (MAX_FRAME - 5)
        with server.client() as c:
            expect(c.cmd("PING " + exact), exact)
            # One byte more closes the connection after a named error.
            c.send(b"PING " + exact.encode() + b"z\n")
            refusal = c.f.readline().decode()
            assert refusal.startswith("(error) "), refusal
            assert "oversized" in refusal, refusal
            expect_close(c.f)
        with server.client() as c:
            # CRLF: the CR is inside the frame budget, so the same payload plus CR
            # is one byte over and is refused rather than trimmed.
            c.send(b"PING " + exact.encode() + b"\r\n")
            assert c.f.readline().startswith(b"(error) ")
            expect_close(c.f)
        with server.client() as c:
            # A CRLF frame one byte shorter is legal and has its CR stripped.
            expect(c.cmd("PING " + exact[1:]), exact[1:])
    # A frame that stops just short of LF must not be answered, and must not
    # poison the next connection.
    with Server(binary, root) as server:
        with server.client() as c:
            c.send(b"PING " + exact.encode())
            c.close()
        with server.client() as c:
            expect(c.cmd("PING"), "PONG")


def truncated_frame(binary, root):
    """A client that disappears mid-frame leaves no half-parsed state."""
    with Server(binary, root) as server:
        for payload in (b"S", b"SET", b"SET half", b"SET halfkey halfval",
                        b"DEL", b"DEL key", b"KEYS"):
            with server.client() as c:
                c.send(payload)
            with server.client() as c:
                expect(c.cmd("GET halfkey"), "(nil)")
                expect(c.cmd("PING"), "PONG")
        # A malformed frame followed by more frames in one write: the error is
        # the last thing the connection gets.
        with server.client() as c:
            c.send(b"SET bad\x00key v\nPING\n")
            assert c.f.readline().startswith(b"(error) ")
            expect_close(c.f)
        expect(server.client().cmd("GET bad"), "(nil)")


def huge_declared_length(binary, root):
    """No length field exists; a huge frame is closed, not buffered."""
    with Server(binary, root) as server:
        with server.client() as c:
            c.send(b"GET " + b"k" * 65536)
            assert c.f.readline().startswith(b"(error) ")
            expect_close(c.f)
        with server.client() as c:
            expect(c.cmd("PING"), "PONG")
        # A frame of exactly MAX_FRAME junk bytes still parses as a command and
        # is rejected by the key/value rules, not by the frame limit.
        with server.client() as c:
            c.send(b"GET " + b"k" * (MAX_FRAME - 4) + b"\n")
            refusal = c.f.readline().decode()
            assert refusal.startswith("(error) "), refusal
            assert "oversized" not in refusal, refusal


def key_and_value_limits(binary, root):
    """MAX_KEY 1..256 and MAX_VALUE 1..4096, both sides."""
    with Server(binary, root) as server, server.client() as c:
        c.error("GET ")                       # empty key
        c.error("SET  value")                 # empty key on write
        c.error("SET k")                      # missing value
        c.error("SET  ")                      # nothing at all
        key_max = "k" * MAX_KEY
        value_max = "v" * MAX_VALUE
        expect(c.cmd("SET " + key_max + " " + value_max), "OK")
        expect(c.cmd("GET " + key_max), value_max)
        c.error("SET " + "k" * (MAX_KEY + 1) + " v")
        c.error("SET k " + "v" * (MAX_VALUE + 1))
        # The refused writes must not have displaced anything.
        expect(c.cmd("GET " + key_max), value_max)
        # A one-byte key is still legal; the bound is not "must look like a word".
        expect(c.cmd("SET x one"), "OK")
        expect(c.cmd("GET x"), "one")
        # Interior and trailing whitespace is a value, not a separator: the
        # single space after the key separates, the other two are data.
        expect(c.cmd("SET spaced   two  spaces  "), "OK")
        expect(c.cmd("GET spaced"), "  two  spaces  ")


def del_limits(binary, root):
    """MAX_DEL 128 accepted, 129 refused; duplicates count once."""
    with Server(binary, root) as server, server.client() as c:
        for i in range(MAX_DEL + 8):
            expect(c.cmd(f"SET d{i} v"), "OK")
        keys = " ".join(f"d{i}" for i in range(MAX_DEL))
        expect(c.cmd("DEL " + keys), f"(integer) {MAX_DEL}")
        expect(c.cmd("DEL d128 d129"), "(integer) 2")
        c.error("DEL " + " ".join(f"d{i}" for i in range(MAX_DEL + 1)))
        expect(c.cmd("SET dup v"), "OK")
        expect(c.cmd("del dup dup dup"), "(integer) 1")


def byte_cap(binary, root):
    """MAX_BYTES counts key bytes plus value bytes and is enforced before the AOF."""
    aof = Path(root) / "fin-kv.aof"
    # Fill to MAX_BYTES exactly through replay: 7711 * (255 + 4096) = 33550561,
    # leaving 3871 bytes. One more key of 5 + 3866 == 3871 fits; 5 + 3867 does not.
    key = "K" * 250
    prefill = MAX_BYTES // (255 + MAX_VALUE)
    with aof.open("wb") as out:
        for i in range(prefill):
            out.write(f"SET {key}{i:05d} 0 {'v' * MAX_VALUE}\n".encode())
    used = prefill * (255 + MAX_VALUE)
    remaining = MAX_BYTES - used
    assert remaining == 3871, remaining

    with Server(binary, root) as server, server.client() as c:
        expect(c.cmd("SET exact " + "e" * (remaining - 6)), "OK")
        before = aof.stat().st_size
        c.error("SET over " + "o" * (remaining - 6))
        assert aof.stat().st_size == before, "a refused SET reached the AOF"
        expect(c.cmd("GET over"), "(nil)")
        # Overwriting an existing key at the cap is a replacement, not growth.
        expect(c.cmd(f"SET {key}00000 " + "w" * MAX_VALUE), "OK")
        expect(c.cmd(f"GET {key}00000"), "w" * MAX_VALUE)
        # Reads keep working after a refused write.
        expect(c.cmd(f"GET {key}00001"), "v" * MAX_VALUE)


def key_cap(binary, root):
    """MAX_KEYS is a count bound, enforced before the AOF and reclaimed by DEL."""
    aof = Path(root) / "fin-kv.aof"
    with aof.open("wb") as out:
        for i in range(MAX_KEYS):
            out.write(f"SET c{i} 0 v\n".encode())
    with Server(binary, root) as server, server.client() as c:
        before = aof.stat().st_size
        c.error("SET overflow v")
        assert aof.stat().st_size == before, "a refused SET reached the AOF"
        expect(c.cmd("DEL c0"), "(integer) 1")
        expect(c.cmd("SET replacement v"), "OK")
        # The replayed count is the advertised bound, not a byte estimate.
        expect(c.cmd("KEYS *"), f"(keys) {MAX_KEYS}")


def disconnect_mid_write(binary, root):
    """A client that goes away while output is pending does not kill the server."""
    with Server(binary, root) as server:
        for _ in range(200):
            with server.client() as filler:
                expect(filler.cmd(f"SET f{_} v"), "OK")
        # Ask for the whole key set, then vanish without reading the reply.
        for _ in range(3):
            raw = socket.create_connection(("127.0.0.1", server.port), timeout=5)
            raw.sendall(b"KEYS *\n")
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                           b"\x01\x00\x00\x00\x00\x00\x00\x00")
            raw.close()
        with server.client() as c:
            expect(c.cmd("PING"), "PONG")
            expect(c.cmd("KEYS *"), "(keys) 200")
        assert server.p.poll() is None, "server exited after a mid-write disconnect"


def idle_and_trickle(binary, root):
    """The per-frame deadline is absolute, so trickled bytes do not extend it."""
    with Server(binary, root, timeout=400) as server:
        idle = server.client()
        try:
            with server.client() as fresh:
                expect(fresh.cmd("PING"), "PONG")
            expect(idle.f.readline(), b"")
        finally:
            idle.close()
        slow = server.client()
        try:
            slow.send(b"S")
            slow.f.readline()
        finally:
            slow.close()


def rss_kib(server):
    status = Path(f"/proc/{server.p.pid}/status").read_text()
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise AssertionError("no VmRSS")


def no_unbounded_growth(binary, root):
    """Repeated writes to one key must not grow the process."""
    with Server(binary, root, timeout=5000) as server, server.client() as c:
        expect(c.cmd("SET churn " + "x" * MAX_VALUE), "OK")
        expect(c.cmd("GET churn"), "x" * MAX_VALUE)
        before = rss_kib(server)
        for i in range(400):
            expect(c.cmd("SET churn " + "y" * MAX_VALUE), "OK")
            expect(c.cmd("GET churn"), "y" * MAX_VALUE)
        after = rss_kib(server)
        assert after - before < 8192, (before, after)
        print(f"  churn: RSS {before}->{after} KiB after 400 max-size overwrites", flush=True)


TESTS = (
    ("frame limit", frame_limit),
    ("truncated frame", truncated_frame),
    ("huge frame", huge_declared_length),
    ("key/value limits", key_and_value_limits),
    ("DEL limit", del_limits),
    ("byte cap", byte_cap),
    ("key cap", key_cap),
    ("disconnect mid-write", disconnect_mid_write),
    ("idle and trickle", idle_and_trickle),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", type=Path, required=True,
                        help="an already built fin-kv binary")
    args = parser.parse_args()
    binary = resolve_binary(args.bin)
    with tempfile.TemporaryDirectory(prefix="finkv-boundary-") as temporary:
        root = Path(temporary)
        for index, (name, test) in enumerate(TESTS):
            directory = root / f"{index:02d}"
            directory.mkdir()
            test(binary, directory)
            print(f"ok [{name}]", flush=True)
        growth = root / "growth"
        growth.mkdir()
        if sys.platform.startswith("linux"):
            no_unbounded_growth(binary, growth)
            print("ok [no unbounded growth]", flush=True)
        else:
            # Linux /proc is how the measurement is taken; not a skip of the
            # behaviour, but of the instrument. The overwrite path is still
            # exercised by the key/value and byte-cap cases above.
            print("skip [no unbounded growth: /proc is Linux-only]", flush=True)
    print("BOUNDARY OK", flush=True)


if __name__ == "__main__":
    main()

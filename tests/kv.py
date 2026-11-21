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

"""Process and socket harness shared by the fin-kv test drivers.

The server is the compiled Fin binary; this module only starts it on an
ephemeral loopback port, waits for its listening banner, and offers a
line-oriented client. It contains no protocol logic of its own, so a test
that passes here has passed against the real Fin implementation.
"""

import os
import re
import select
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READY = re.compile(rb"listening on 127\.0\.0\.1:(\d+)")


def resolve_binary(path):
    """The executable the compiler actually wrote.

    `finc -o out/fin-kv` appends `.exe` on Windows, so a caller that names the
    output without the suffix still finds it. Every other platform is exact.
    """
    # Absolute, because the server runs with cwd set to its data directory.
    candidate = Path(path).resolve()
    if candidate.exists():
        return candidate
    with_exe = candidate.with_suffix(candidate.suffix + ".exe")
    if with_exe.exists():
        return with_exe
    raise FileNotFoundError(f"no built binary at {candidate}")


class Client:
    """One LF-terminated request/response connection."""

    def __init__(self, port):
        import socket

        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.f = self.s.makefile("rb")

    def send(self, payload):
        self.s.sendall(payload)

    def cmd(self, line):
        self.s.sendall(line.encode() + b"\n")
        return self.f.readline().decode().rstrip("\n")

    def error(self, line):
        answer = self.cmd(line)
        assert answer.startswith("(error) "), (line, answer)
        return answer

    def close(self):
        self.f.close()
        self.s.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Server:
    """The compiled fin-kv binary, running in a scratch data directory."""

    def __init__(self, binary, directory, *, timeout=700, env=None, ready=True):
        self.directory = Path(directory)
        self.aof = self.directory / "fin-kv.aof"
        settings = os.environ.copy()
        settings.update(FIN_KV_PORT="0", FIN_KV_TIMEOUT_MS=str(timeout))
        settings.update(env or {})
        self.p = subprocess.Popen([str(binary)], cwd=directory, env=settings,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.output = b""
        self.port = None
        if ready:
            try:
                self.wait_ready()
            except BaseException:
                self.stop()
                raise

    def wait_ready(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if select.select([self.p.stdout], [], [], 0.1)[0]:
                data = os.read(self.p.stdout.fileno(), 4096)
                self.output += data
                match = READY.search(self.output)
                if match:
                    self.port = int(match[1])
                    return
            if self.p.poll() is not None:
                raise AssertionError(f"server exited {self.p.returncode}: {self.output!r}")
        raise AssertionError(f"startup deadline: {self.output!r}")

    def client(self):
        return Client(self.port)

    def stop(self, kill=False):
        if self.p.poll() is None:
            self.p.kill() if kill else self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=5)
        self.p.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()


def expect(actual, wanted):
    assert actual == wanted, (actual, wanted)


def expect_close(reader):
    """A connection the server closed reads EOF, or resets if bytes were unread.

    Both are a closed connection from the client's side. The reset only happens
    when the client had already written bytes the server refused to parse, which
    is exactly the oversized-frame case.
    """
    try:
        assert reader.readline() == b"", "connection stayed open"
    except ConnectionResetError:
        pass

#!/usr/bin/env python3
"""fin-kv smoke test: builds the server, exercises the full line protocol over
a real socket, verifies key expiry and AOF restart persistence.

Usage:  python3 tests/smoke.py        (run from the repo root)
Requires: a working `finc` on PATH. Fails loudly on any step.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 7379


def sh(cmd, **kw):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


class Client:
    def __init__(self):
        self.s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
        self.f = self.s.makefile("r", newline="\n")

    def cmd(self, line):
        self.s.sendall((line + "\n").encode())
        return self.f.readline().rstrip("\n")

    def cmd_multi(self, line, extra_lines):
        """For KEYS: read the header, then `extra_lines` key lines."""
        head = self.cmd(line)
        keys = [self.f.readline().rstrip("\n") for _ in range(extra_lines)]
        return head, keys

    def close(self):
        self.f.close()
        self.s.close()


def expect(actual, wanted, what):
    if actual != wanted:
        print(f"FAIL [{what}]: got {actual!r}, want {wanted!r}")
        sys.exit(1)
    print(f"ok [{what}] -> {actual!r}")


def main():
    if shutil.which("finc") is None:
        print("SKIP: finc not on PATH (toolchain not installed yet)")
        sys.exit(3)
    # The server binds a fixed port; refuse to run if something is there.
    probe = socket.socket()
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"SKIP: something already listens on 127.0.0.1:{PORT}")
        sys.exit(3)
    probe.close()

    workdir = tempfile.mkdtemp(prefix="finkv-smoke-")
    sh(["finc", "src/main.fin", "-o", os.path.join(workdir, "fin-kv")], cwd=ROOT)

    servers = []

    def spawn_server():
        p = subprocess.Popen(
            # stdbuf: the fin binary's libc stdout is fully buffered on a pipe,
            # so the listen banner would never reach the harness without it.
            ["stdbuf", "-o0", "-e0", os.path.join(workdir, "fin-kv")],
            cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        servers.append(p)
        return p

    def wait_banner(p, want):
        """Read stdout lines until one contains `want` (or the server dies)."""
        deadline = time.time() + 15
        while time.time() < deadline:
            if p.poll() is not None:
                raise AssertionError(f"server exited (rc={p.returncode}) before banner {want!r}")
            line = p.stdout.readline()
            if want in line:
                return line.strip()
        raise AssertionError(f"timed out waiting for banner {want!r}")

    def wait_port_open(timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            pr = socket.socket()
            try:
                if pr.connect_ex(("127.0.0.1", PORT)) == 0:
                    return True
            finally:
                pr.close()
            time.sleep(0.2)
        return False

    def wait_port_bindable(timeout=75):
        """Wait until nothing (not even a TIME-WAIT) blocks rebinding PORT.

        The server closes its accepted sockets first on QUIT, so its side
        lingers in TIME-WAIT (~60s); without SO_REUSEADDR in stdlib
        Socket::serve, a rebind inside that window fails with EADDRINUSE.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            pr = socket.socket()
            try:
                pr.bind(("0.0.0.0", PORT))
                return True
            except OSError:
                time.sleep(1)
            finally:
                pr.close()
        return False

    # A previous run's TIME-WAIT can still block the bind; wait it out.
    assert wait_port_bindable(), f"port {PORT} never became bindable"
    server = spawn_server()
    try:
        # The server prints an AOF-replay line (or "no aof yet") before the
        # listen banner; skip to the banner, then confirm the port accepts.
        banner = wait_banner(server, "listening")
        assert "fin-kv" in banner, banner
        assert wait_port_open(), "port never opened after listen banner"
        print("ok [server banner] ->", banner)

        c = Client()
        expect(c.cmd("PING"), "PONG", "PING")
        expect(c.cmd("ping hello"), "hello", "PING with message")
        expect(c.cmd("SET greeting hello world"), "OK", "SET with spaces")
        expect(c.cmd("GET greeting"), "hello world", "GET")
        expect(c.cmd("GET missing"), "(nil)", "GET missing")
        expect(c.cmd("EXISTS greeting"), "(integer) 1", "EXISTS hit")
        expect(c.cmd("EXISTS missing"), "(integer) 0", "EXISTS miss")
        expect(c.cmd("TTL greeting"), "(integer) -1", "TTL no expiry")
        expect(c.cmd("TTL missing"), "(integer) -2", "TTL no key")
        expect(c.cmd("SET temp xyz EX 1"), "OK", "SET with EX")
        ttl = c.cmd("TTL temp")
        assert ttl in ("(integer) 1", "(integer) 0"), ttl
        print("ok [TTL expiring] ->", ttl)
        time.sleep(1.2)
        expect(c.cmd("GET temp"), "(nil)", "GET after expiry")
        expect(c.cmd("SET a 1"), "OK", "SET a")
        expect(c.cmd("SET b 2"), "OK", "SET b")
        head, keys = c.cmd_multi("KEYS *", 3)
        expect(head, "(keys) 3", "KEYS header")
        assert sorted(keys) == ["a", "b", "greeting"], keys
        print("ok [KEYS body] ->", sorted(keys))
        expect(c.cmd("DEL a b missing"), "(integer) 2", "DEL multi")
        expect(c.cmd("GET a"), "(nil)", "GET after DEL")
        expect(c.cmd("BOGUS"), "(error) unknown command 'BOGUS'", "unknown cmd")
        expect(c.cmd("GET"), "(error) wrong number of arguments for 'get'", "bad arity")
        expect(c.cmd("SET onlykey"), "(error) wrong number of arguments for 'set'", "SET arity")
        expect(c.cmd("KEYS foo*"), "(error) only KEYS * is supported", "KEYS pattern")
        expect(c.cmd(""), "", "blank line ignored")
        expect(c.cmd("QUIT"), "OK", "QUIT")
        c.close()

        # Restart: the AOF must bring the dataset back (minus expired keys).
        server.terminate()
        server.wait(timeout=10)
        # The server closed its client sockets first, so 7379 lingers in
        # TIME-WAIT; the rebind needs the window to clear (see wait_port_bindable).
        assert wait_port_bindable(), f"port {PORT} never became bindable after restart"
        server = spawn_server()
        banner2 = wait_banner(server, "replayed")
        print("ok [AOF replay banner] ->", banner2)
        # The bind happens just after the replay banner; wait for the port.
        assert wait_port_open(), "server never came back after restart"
        c2 = Client()
        expect(c2.cmd("GET greeting"), "hello world", "GET after restart")
        expect(c2.cmd("GET temp"), "(nil)", "expired key not resurrected")
        expect(c2.cmd("QUIT"), "OK", "QUIT 2")
        c2.close()
    finally:
        for p in servers:
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        shutil.rmtree(workdir, ignore_errors=True)

    print("SMOKE OK")


if __name__ == "__main__":
    main()

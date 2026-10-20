#!/usr/bin/env python3
"""Build/run the real Fin server; reusable against an independently built binary."""
import argparse
import os
from pathlib import Path
import re
import resource
import select
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


class Client:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.f = self.s.makefile("rb")

    def cmd(self, line):
        self.s.sendall(line.encode() + b"\n")
        return self.f.readline().decode().rstrip("\n")

    def error(self, line):
        answer = self.cmd(line)
        assert answer.startswith("(error) "), (line, answer)

    def close(self):
        self.f.close()
        self.s.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Server:
    def __init__(self, binary, directory, *, timeout=700, env=None, ready=True):
        self.directory = Path(directory)
        self.aof = self.directory / "fin-kv.aof"
        settings = os.environ.copy()
        settings.update(FIN_KV_PORT="0", FIN_KV_TIMEOUT_MS=str(timeout))
        settings.update(env or {})
        self.p = subprocess.Popen([str(binary)], cwd=directory, env=settings,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.output = b""
        if ready:
            try:
                self.wait_ready()
            except BaseException:
                self.stop()
                raise

    def wait_ready(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if select.select([self.p.stdout], [], [], 0.1)[0]:
                data = os.read(self.p.stdout.fileno(), 4096)
                self.output += data
                match = re.search(rb"listening on 127\.0\.0\.1:(\d+)", self.output)
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


def protocol(binary, root):
    with Server(binary, root) as server:
        with server.client() as c:
            expect(c.cmd("PING"), "PONG")
            expect(c.cmd("ping hello"), "hello")
            expect(c.cmd("SET greeting hello world"), "OK")
            expect(c.cmd("GET greeting"), "hello world")
            expect(c.cmd("GET missing"), "(nil)")
            expect(c.cmd("EXISTS greeting"), "(integer) 1")
            expect(c.cmd("EXISTS missing"), "(integer) 0")
            expect(c.cmd("TTL greeting"), "(integer) -1")
            expect(c.cmd("TTL missing"), "(integer) -2")
            expect(c.cmd("SET temp xyz EX 1"), "OK")
            assert c.cmd("TTL temp") in ("(integer) 0", "(integer) 1")
            # Expiry must work even when no client reads the expired replacement.
            expect(c.cmd("SET replaced original"), "OK")
            expect(c.cmd("SET replaced expiring EX 1"), "OK")
            expect(c.cmd("SET a 1"), "OK")
            expect(c.cmd("SET b 2"), "OK")
            expect(c.cmd("DEL a b missing a"), "(integer) 2")
            expect(c.cmd("GET a"), "(nil)")
            for bad in ("BOGUS", "GET", "SET onlykey", "KEYS foo*", "QUIT extra"):
                c.error(bad)
            expect(c.cmd(""), "")
            expect(c.cmd("QUIT"), "OK")
        # Unlike the old implementation there is no TIME_WAIT rebind wait.
        time.sleep(1.2)
        with server.client() as c:
            expect(c.cmd("GET temp"), "(nil)")
            expect(c.cmd("KEYS *"), "(keys) 1")
            expect(c.f.readline().decode().strip(), "greeting")
        server.stop(kill=True)
    with Server(binary, root) as server, server.client() as c:
        expect(c.cmd("GET greeting"), "hello world")
        expect(c.cmd("GET temp"), "(nil)")
        expect(c.cmd("GET replaced"), "(nil)")
    # A separate fixture proves replay itself removes an older value.
    (root / "fin-kv.aof").write_bytes(b"SET old 0 original\nSET old 1 expired\n")
    with Server(binary, root) as server, server.client() as c:
        expect(c.cmd("GET old"), "(nil)")


def recovery(binary, root):
    aof = root / "fin-kv.aof"
    base = b"SET stable 0 yes\n"
    for tail in (b"S", b"SET", b"SET ", b"SET key", b"SET key ", b"SET key 0 ",
                 b"SET key 0 incomplete", b"DEL ", b"DEL stable", b"DEL stable "):
        aof.write_bytes(base + tail)
        with Server(binary, root) as server, server.client() as c:
            expect(aof.read_bytes(), base)
            expect(c.cmd("SET survivor ok"), "OK")
            expect(c.cmd("GET stable"), "yes")
        with Server(binary, root) as server, server.client() as c:
            expect(c.cmd("GET survivor"), "ok")
    corrupt = (b"BOGUS\n", b"SET k nope value\n", b"SET k 9223372036854775808 v\n",
               b"SET  0 v\n", b"SET k 0 \n", b"DEL\n", b"DEL a  b\n", b"\n",
               b"SET k 0 a\x00b\n", b"SET k 0 a\rb\n", b"SET k 0 " + b"x" * 4097 + b"\n",
               b"garbage", b"SET k nope", b"SET  ", b"DEL a  ",
               b"SET k 0 " + b"x" * 4097,
               b"SET k " + b"0" * 20, b"SET k " + b"0" * 20 + b" v\n",
               b"DEL " + b" ".join(b"key" + str(i).encode() for i in range(128)) + b" ")
    for bad in corrupt:
        original = base + bad
        aof.write_bytes(original)
        with Server(binary, root, ready=False) as server:
            output, _ = server.p.communicate(timeout=10)
            assert server.p.returncode != 0, (bad, output)
        expect(aof.read_bytes(), original)
    # Valid-looking tail after an invalid complete record must not be repaired.
    original = base + b"INVALID\nSET partial"
    aof.write_bytes(original)
    with Server(binary, root, ready=False) as server:
        server.p.communicate(timeout=10)
        assert server.p.returncode != 0
    expect(aof.read_bytes(), original)
    aof.unlink()
    aof.mkdir()
    with Server(binary, root, ready=False) as server:
        server.p.communicate(timeout=10)
        assert server.p.returncode != 0
    aof.rmdir()
    os.mkfifo(aof)
    with Server(binary, root, ready=False) as server:
        server.p.communicate(timeout=5)
        assert server.p.returncode != 0
    assert aof.is_fifo()
    aof.unlink()
    aof.symlink_to("/dev/full")
    with Server(binary, root, ready=False) as server:
        server.p.communicate(timeout=5)
        assert server.p.returncode != 0
    assert aof.is_symlink()
    aof.unlink()
    aof.write_bytes(base)
    aof.chmod(0)
    try:
        if os.geteuid() != 0:
            with Server(binary, root, ready=False) as server:
                server.p.communicate(timeout=10)
                assert server.p.returncode != 0
    finally:
        aof.chmod(0o600)
    expect(aof.read_bytes(), base)
    with Server(binary, root) as first:
        with Server(binary, root, ready=False) as second:
            second.p.communicate(timeout=10)
            assert second.p.returncode != 0
        with first.client() as c:
            expect(c.cmd("GET stable"), "yes")


# Fault injection is test-only libc interposition, never an alternate store.
# Actual writes to /dev/full exercise ENOSPC. Other modes cover partial writes
# and a failing sync after the real record has already reached the page cache.
FAULT_SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static int target(int fd) {
    char link[64], path[4096];
    snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
    ssize_t n = readlink(link, path, sizeof path - 1);
    if (n < 0) return 0;
    path[n] = 0;
    size_t size = strlen(path);
    return size >= 10 && !strcmp(path + size - 10, "fin-kv.aof");
}
static int triggered(void) {
    const char *p = getenv("FIN_KV_TEST_MARKER");
    return p && access(p, F_OK) == 0;
}
size_t fwrite(const void *p, size_t size, size_t count, FILE *stream) {
    size_t (*real_write)(const void *, size_t, size_t, FILE *) = dlsym(RTLD_NEXT, "fwrite");
    const char *mode = getenv("FIN_KV_TEST_MODE");
    if (mode && triggered() && target(fileno(stream))) {
        if (!strcmp(mode, "full")) {
            int full = open("/dev/full", O_WRONLY);
            ssize_t n = write(full, p, size * count);
            close(full);
            if (n < 0) return 0;
            abort();
        }
        if (!strcmp(mode, "direct_full")) {
            int full = open("/dev/full", O_WRONLY);
            if (full < 0 || dup2(full, fileno(stream)) < 0) abort();
            close(full);
            return real_write(p, size, count, stream);
        }
        if (!strcmp(mode, "partial")) {
            real_write(p, 1, count / 2, stream);
            errno = EIO;
            return count / 2;
        }
    }
    return real_write(p, size, count, stream);
}
int fflush(FILE *stream) {
    int (*real_flush)(FILE *) = dlsym(RTLD_NEXT, "fflush");
    int result = real_flush(stream);
    const char *mode = getenv("FIN_KV_TEST_MODE");
    if (stream && mode && !strcmp(mode, "flush") && triggered() && target(fileno(stream))) {
        errno = EIO;
        return EOF;
    }
    return result;
}
int fsync(int fd) {
    int (*real_sync)(int) = dlsym(RTLD_NEXT, "fsync");
    static int failed = 0;
    const char *mode = getenv("FIN_KV_TEST_MODE");
    if (!failed && mode && !strcmp(mode, "sync") && triggered() && target(fd)) {
        failed = 1;
        errno = EIO;
        return -1;
    }
    return real_sync(fd);
}
'''


def persistence_faults(binary, root, shim):
    aof = root / "fin-kv.aof"
    marker = root / "fail"
    for mode in ("full", "partial", "flush", "sync"):
        for command in ("SET stable changed", "DEL stable second"):
            original = b"SET stable 0 original\nSET second 0 intact\n"
            aof.write_bytes(original)
            env = {"LD_PRELOAD": str(shim), "FIN_KV_TEST_MODE": mode,
                   "FIN_KV_TEST_MARKER": str(marker)}
            with Server(binary, root, env=env) as server, server.client() as c:
                marker.touch()
                c.error(command)
                expect(c.cmd("GET stable"), "original")
                expect(c.cmd("GET second"), "intact")
                # Writer stays fail-closed even after the device appears recovered.
                marker.unlink()
                c.error("SET later no")
                expect(c.cmd("GET later"), "(nil)")
            expect(aof.read_bytes(), original)
            with Server(binary, root) as server, server.client() as c:
                expect(c.cmd("GET stable"), "original")
                expect(c.cmd("GET second"), "intact")
    direct_env = {"LD_PRELOAD": str(shim), "FIN_KV_TEST_MODE": "direct_full",
                  "FIN_KV_TEST_MARKER": str(marker)}
    with Server(binary, root, env=direct_env) as server:
        with server.client() as c:
            expect(c.cmd("SET direct_full original"), "OK")
            # Test-only fault injection replaces the already-open AOF descriptor
            # with /dev/full (real ENOSPC, not a mocked success/failure return).
            marker.touch()
            c.error("SET direct_full changed")
            expect(c.cmd("GET direct_full"), "original")
            c.error("DEL direct_full")
            expect(c.cmd("GET direct_full"), "original")
    marker.unlink()
    with Server(binary, root) as server, server.client() as c:
        expect(c.cmd("GET direct_full"), "original")


def inputs(binary, root):
    with Server(binary, root, timeout=400) as server:
        with server.client() as c:
            for seconds in ("2147483648", "999999999999999999999999", "0", "-1", "nope"):
                c.error("SET invalid x EX " + seconds)
                expect(c.cmd("GET invalid"), "(nil)")
                expect(c.cmd("PING"), "PONG")
            c.error("SET k v EX")
            c.error("SET " + "k" * 257 + " v")
            c.error("SET k " + "v" * 4097)
            c.error("SET  emptykey")
            key, value = "k" * 256, "v" * 4096
            expect(c.cmd(f"SET {key} {value}"), "OK")
            expect(c.cmd(f"GET {key}"), value)
            expect(c.cmd("SET spaces  leading  internal trailing  "), "OK")
            expect(c.cmd("GET spaces"), " leading  internal trailing  ")
            expect(c.cmd("SET maximum lifetime EX 2147483647"), "OK")
            expect(c.cmd("SET dup 1"), "OK")
            expect(c.cmd("del dup dup"), "(integer) 1")
            c.error("DEL " + " ".join("key" + str(i) for i in range(129)))
            c.s.sendall(b"PI")
            c.s.sendall(b"NG\r\nPING two\n")
            expect(c.f.readline(), b"PONG\n")
            expect(c.f.readline(), b"two\n")
        for raw in (b"SET nul a\x00b\n", b"SET nul a\rb\n", b"x" * 8193):
            with server.client() as c:
                c.s.sendall(raw)
                assert c.f.readline().startswith(b"(error) ")
                expect(c.f.readline(), b"")
            with server.client() as c:
                expect(c.cmd("GET nul"), "(nil)")
                expect(c.cmd("PING"), "PONG")
        # An idle accepted socket cannot monopolize this single-client server.
        idle = server.client()
        try:
            with server.client() as next_client:
                start = time.monotonic()
                expect(next_client.cmd("PING"), "PONG")
                assert time.monotonic() - start < 2
            expect(idle.f.readline(), b"")
        finally:
            idle.close()
        # Deadline is per complete frame, not reset by a trickle of bytes.
        slow = server.client()
        try:
            slow.s.sendall(b"S")
            time.sleep(0.25)
            slow.s.sendall(b"E")
            time.sleep(0.25)
            expect(slow.f.readline(), b"")
        finally:
            slow.close()
        # Peer resets while output is pending must not deliver SIGPIPE.
        with server.client() as c:
            c.s.sendall(b"PING\n" * 100)
        with server.client() as c:
            expect(c.cmd("PING"), "PONG")
    for port in ("65536", "-1", "999999999999999999999", "", "nope"):
        with Server(binary, root, env={"FIN_KV_PORT": port}, ready=False) as server:
            server.p.communicate(timeout=5)
            assert server.p.returncode != 0
    for timeout in ("0", "-1", "60001", "999999999999999999999", ""):
        with Server(binary, root, env={"FIN_KV_TIMEOUT_MS": timeout}, ready=False) as server:
            server.p.communicate(timeout=5)
            assert server.p.returncode != 0
    with Server(binary, root) as server, server.client() as c:
        expect(c.cmd("GET " + "k" * 256), "v" * 4096)
        expect(c.cmd("GET spaces"), " leading  internal trailing  ")
        expect(c.cmd("GET maximum"), "lifetime")
        expect(c.cmd("GET dup"), "(nil)")


def rss_kib(server):
    status = Path(f"/proc/{server.p.pid}/status").read_text()
    return int(re.search(r"VmRSS:\s+(\d+)", status)[1])


def large_replay(binary, root):
    aof = root / "fin-kv.aof"
    # 100k records, 13 MB, only 128 live keys. Repeated overwrite ownership and
    # streaming must not retain log bytes or allocate per historical operation.
    with aof.open("wb") as out:
        for i in range(100000):
            out.write(f"SET key{i % 128} 0 {i:06d}".encode() + b"x" * 112 + b"\n")
    start = time.monotonic()
    with Server(binary, root, timeout=5000) as server, server.client() as c:
        elapsed = time.monotonic() - start
        expect(c.cmd("GET key31"), "099999" + "x" * 112)
        assert rss_kib(server) < 64000, rss_kib(server)
        before = rss_kib(server)
        # Exercise SET overwrite, DEL, tombstone reuse, replies and KEYS without
        # thousands of fsyncs: reads/KEYS expose per-request allocation leaks.
        for batch in range(20):
            c.s.sendall(b"GET key31\n" * 500)
            for _ in range(500):
                expect(c.f.readline(), ("099999" + "x" * 112 + "\n").encode())
        for _ in range(50):
            expect(c.cmd("SET churn value"), "OK")
            expect(c.cmd("SET churn next"), "OK")
            expect(c.cmd("DEL churn"), "(integer) 1")
        after = rss_kib(server)
        assert after - before < 8192, (before, after)
        c.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        for _ in range(20):
            expect(c.cmd("KEYS *"), "(keys) 128")
            keys = [c.f.readline().decode().strip() for _ in range(128)]
            assert sorted(keys) == sorted(f"key{i}" for i in range(128))
        print(f"  large replay: {elapsed:.2f}s, RSS {before}->{after} KiB")
    # Full bounded table fails before appending or allocating another value.
    with aof.open("wb") as out:
        for i in range(8192):
            out.write(f"SET capacity{i} 0 v\n".encode())
    with Server(binary, root) as server, server.client() as c:
        size = aof.stat().st_size
        c.error("SET overflow v")
        expect(aof.stat().st_size, size)
        expect(c.cmd("DEL capacity0"), "(integer) 1")
        expect(c.cmd("SET replacement v"), "OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default=os.environ.get("FINC", "finc"))
    parser.add_argument("--fin-libs", default=os.environ.get("FIN_LIBS"))
    parser.add_argument("--bin", type=Path, help="reuse binary, skip Fin compilation")
    parser.add_argument("--opt", choices=("O0", "O2"), default="O0")
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryDirectory(prefix="finkv-test-") as temporary:
        work = Path(temporary)
        binary = args.bin.resolve() if args.bin else work / "fin-kv"
        if not args.bin:
            command = [args.compiler, str(ROOT / "src/main.fin"), "-o", str(binary), "-" + args.opt]
            if args.fin_libs:
                command += ["--fin-libs", args.fin_libs]
            subprocess.run(command, check=True, timeout=120)
        shim_source = work / "faults.c"
        shim = work / "faults.so"
        shim_source.write_text(FAULT_SHIM)
        subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", "-O2",
                        str(shim_source), "-o", str(shim), "-ldl"], check=True, timeout=30)
        for name, test in (("protocol/restart", protocol), ("recovery/corruption", recovery),
                           ("input/deadline", inputs), ("large replay/ownership", large_replay)):
            directory = work / name.split("/")[0]
            directory.mkdir()
            test(binary, directory)
            print(f"ok [{name}]", flush=True)
        directory = work / "faults"
        directory.mkdir()
        persistence_faults(binary, directory, shim)
        print("ok [/dev/full, partial write, flush, fsync; SET/DEL unchanged]", flush=True)
    print("SMOKE OK", flush=True)


if __name__ == "__main__":
    main()

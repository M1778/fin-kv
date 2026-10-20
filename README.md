# fin-kv

A Redis-lite key-value server written in [Fin](https://github.com/M1778/Fin) —
Builder #3's first real-world app. TCP server, line protocol, in-memory store,
key expiry, and **append-only log persistence** (replay on startup).

> **Status: UNVERIFIED.** No working `finc` binary exists in the build
> environment yet (needs LLVM 22; install in progress), so this code has not
> been through `finc` check / codegen / run. It is written strictly against
> `docs/agent-guide.md` (Fin v0.4.0) and the stdlib sources. See
> [Verification](#verification).

## What it proves

Fin can do infrastructure software: a stateful TCP server with a hand-rolled
protocol parser, an in-memory hash map, wall-clock expiry, and crash-safe
persistence — using **only the standard library** (`networking`, `hashmap`,
`fs`, `time`, `strings`).

## Protocol

One command per line, `\n`-terminated (`\r\n` tolerated). Responses are
single lines unless noted.

| Command | Response | Notes |
|---|---|---|
| `PING` | `PONG` | |
| `PING <msg>` | `<msg>` | echo |
| `SET <k> <v>` | `OK` | value may contain spaces |
| `SET <k> <v> EX <s>` | `OK` | key expires after `<s>` seconds |
| `GET <k>` | `<v>` / `(nil)` | |
| `DEL <k1> [k2 ...]` | `(integer) <n>` | number of keys deleted |
| `EXISTS <k>` | `(integer) 0` / `1` | |
| `TTL <k>` | `(integer) <s>` / `-1` / `-2` | seconds left; `-1` = no expiry; `-2` = no key |
| `KEYS *` | `(keys) <n>` then `<n>` key lines | only `*` is supported |
| `QUIT` | `OK` | then the connection closes |
| anything else | `(error) <message>` | malformed input never crashes the server |

Commands are case-insensitive. Blank lines are ignored.

## Build & run

```sh
finc src/main.fin          # frontend check
finc src/main.fin -o fin-kv  # codegen + link
./fin-kv                   # listens on 127.0.0.1:7379
```

Talk to it:

```sh
$ nc 127.0.0.1 7379
PING
PONG
SET greeting hello world
OK
GET greeting
hello world
SET session abc123 EX 60
OK
TTL session
(integer) 59
KEYS *
(keys) 2
greeting
session
QUIT
OK
```

Persistence: every `SET`/`DEL` is appended to `fin-kv.aof` in the working
directory as `SET <key> <expires_epoch> <value>` / `DEL <key>`. On startup the
log is replayed; entries whose expiry already passed are skipped. Kill the
server and restart it — your keys are still there.

## Smoke test

`tests/smoke.py` builds the server, starts it (fixed port 7379 — the test
checks the port is free first), runs a full protocol script over a socket
(SET/GET/DEL/EXISTS/TTL/KEYS/PING/QUIT, key expiry, and restart persistence
via the AOF), and asserts every reply:

```sh
finn do smoke        # or: python3 tests/smoke.py
```

## Design notes

- **No `blame` on client input.** `blame` aborts the process; every malformed
  command gets an `(error)` reply. (`parse_int` blames on garbage, so `EX`
  seconds are guarded by `is_numeric` first; `HashMap.__get` blames on a
  missing key, so every read is guarded by `exists`.)
- **Expiry is lazy:** checked on `GET`/`EXISTS`/`TTL`/`KEYS`/`DEL` and at AOF
  replay. No background sweeper (single-threaded server).
- **Store = two maps** (`string→string` values, `string→long` expiry epochs)
  rather than one map of structs, to avoid struct-copy ownership questions in
  unverified code.
- **Exact-count `recv`:** `networking::std`'s `recv_bytes` cannot distinguish
  a 0-byte read (peer closed) from a full one, so `main.fin` declares its own
  `recv` binding (`kv_recv`) and reads `conn.fd` directly. If `Socket.fd`
  proves inaccessible once `finc` lands, the documented fallback is
  `recv_bytes(1)` (there, length 1 unambiguously means EOF).
- **Single-threaded:** one client is served at a time. Concurrency needs the
  threading story from the library builders — a future milestone.

## Known limitations (v0.1.0)

- UNVERIFIED — see above.
- Transient per-command allocations (split token lists, reply strings) are
  not reclaimed; the store itself is bounded by client data. A long-running
  server will grow slowly. (Deliberate: manual frees in unverified code risk
  use-after-free; this gets tightened once builds are proven.)
- `KEYS` only supports `*`; no pattern matching.
- Values may not contain newlines (line protocol); keys may not be empty.
- The AOF grows without bound — no compaction/rewrite yet.
- Fixed port 7379, binds all interfaces (`Socket::serve` semantics).

## Verification

- [ ] `finc src/main.fin` — frontend check passes
- [ ] `finc src/main.fin -o fin-kv` — codegen + link passes
- [ ] `python3 tests/smoke.py` — full protocol + persistence assertions pass
- [ ] `Socket.fd` field access from app code (else switch to documented fallback)

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

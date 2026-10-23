# fin-kv

A single-process key-value TCP server implemented in **Fin**, with an owned,
bounded in-memory hash table and an append-only log (AOF). Python is only the
integration-test driver; all parsing, storage, recovery and networking execute
in the compiled Fin binary.

## Build and test

The implementation is verified on Linux x86-64 and ARM64 with Fin 0.4.0
(contract 3), at both `-O0` and `-O2`, including the full durability/recovery suite
in [native run 39508662175](https://github.com/M1778/fin-sdk/actions/runs/39508662175).
Native Windows and macOS socket/file branches are now implemented and pass
frontend/codegen branch checks. Linux x86_64 and native Windows x86_64 pass
the O0/O2 functional, recovery, replay and deadline suites. Linux additionally
passes partial-write/flush/fsync fault injection; that injection is explicitly
skipped on Windows. Native macOS and fresh Linux ARM64 validation remain pending.
Windows uses Winsock, UCRT binary file I/O and native file locking;
macOS uses its own errno, socket flags and stat layout.

```sh
finc --fin-libs /path/to/lib/std src/main.fin
finc --fin-libs /path/to/lib/std src/main.fin -O2 -o /tmp/fin-kv
python3 -I tests/smoke.py --compiler /path/to/finc --fin-libs /path/to/lib/std --opt O0
python3 -I tests/smoke.py --compiler /path/to/finc --fin-libs /path/to/lib/std --opt O2
```

An independent test runner can reuse an already built binary:

```sh
python3 -I tests/smoke.py --bin /tmp/fin-kv
```

The suite uses temporary working directories and ephemeral loopback ports.
It requires Python 3. On Linux, a C compiler (`CC`, default `cc`) builds a
**test-only fault injector** using `/proc` and `LD_PRELOAD`. Other hosts run the
functional/recovery suites and explicitly skip Linux-specific fault injection
and unsupported memory sampling. Tests cover normal commands, restart and
expiry, corrupt/torn logs, 100,000-record replay, bounded memory, table capacity,
NUL/oversized input, overflow, idle/trickling clients, and failed writes.
The injector routes writes to `/dev/full` and simulates partial-write, flush
and fsync errors; it does not replace or simulate the Fin storage engine.

## Run

Run the binary in a directory dedicated to its data:

```sh
/path/to/fin-kv
# In another terminal:
nc 127.0.0.1 7379
```

- `FIN_KV_PORT`: decimal `0..65535`, default `7379`; `0` chooses an ephemeral
  port reported in the listening banner.
- `FIN_KV_TIMEOUT_MS`: decimal `1..60000`, default `5000`.
- Listener binds **127.0.0.1 only**, not every interface.
- `fin-kv.aof` lives in the working directory. POSIX creates it with mode `0600`;
  Windows inherits the data directory's ACL. Existing permissions are not changed.
  Symlinks/reparse points and nonregular files are refused.
- An exclusive advisory lock refuses a second cooperating writer to that AOF.

There is no authentication or TLS. Loopback is not an authorization boundary
against other local users. Keep the data directory private and do not expose
this service through a public proxy without separate access control.

## Protocol and limits

Commands are case-insensitive, one per LF-terminated line. CRLF is accepted.
Other control bytes, embedded NUL and DEL bytes are rejected; an invalid or
oversized frame receives an error and the connection closes.

| Command | Response |
|---|---|
| `PING [message]` | `PONG` or the message |
| `SET key value` | `OK` |
| `SET key value EX seconds` | `OK` |
| `GET key` | value or `(nil)` |
| `DEL key [key ...]` | `(integer) N` |
| `EXISTS key` | `(integer) 0` or `(integer) 1` |
| `TTL key` | seconds remaining, `-1` without expiry, `-2` if missing |
| `KEYS *` | `(keys) N`, followed by N key lines |
| `QUIT` | `OK`, then connection closes |
| blank line | blank response line |
| invalid command/arguments | `(error) message` |

Limits are checked before persistence or mutation:

- Frame: 8192 bytes before LF, including an optional CR.
- Key: 1–256 bytes, no whitespace/control bytes.
- Value: 1–4096 bytes. Internal and trailing spaces are preserved. A terminal
  `EX seconds` is reserved syntax; seconds must be `1..2147483647`. Invalid,
  missing or overflowing expiry arguments produce errors, never assertions.
- DEL: at most 128 keys; duplicate keys count only once.
- Store: at most 8192 entries and 32 MiB of key/value bytes, plus fixed hash
  arrays and bounded request/reply buffers. There is no automatic eviction.
- Expiry is lazy on reads and KEYS. Expired entries can consume capacity until
  visited; `KEYS *` sweeps them. Expiry uses the wall clock and follows clock
  adjustments. Keys are not resurrected by replaying older values.

One client is served at a time. Each complete input frame and each response has
an absolute deadline; trickled bytes do not reset the frame deadline. Idle or
slow clients are disconnected. A continually active client can still delay
others: this is not a concurrent or distributed server.

## Persistence contract

AOF records are `SET key absolute_epoch value\n` (`0` means no expiry), or
`DEL key [key ...]\n`. Existing single-key DEL records remain supported.
A multi-key DEL is one record, not separately acknowledged partial mutations.

For every accepted SET/DEL:

1. Validate syntax and capacity without changing the dataset.
2. Append the complete record and check its byte count.
3. Check `fflush`, then `fsync` the AOF descriptor.
4. Only then apply the in-memory mutation and return success.

A failed write/flush/sync leaves the command unapplied in memory and returns an
error. The writer attempts truncation to its last committed offset and fsyncs
that rollback. It remains **write-disabled until restart**, even if rollback
succeeds; reads continue. Initial AOF creation also fsyncs the parent directory
on Linux. macOS warns when its filesystem refuses directory fsync. Windows
flushes file data with `_commit` but exposes no separate directory-fsync guarantee;
new directory-entry durability is not promised there.

Startup streams the log with fixed-size buffers rather than loading it whole.
Every complete record is validated, including lengths and checked 64-bit epoch
parsing. An expired SET removes any earlier version of that key. Unreadable
logs, invalid complete records, invalid tail prefixes, or capacity violations
cause a nonzero exit before listening. Corrupt logs are not rewritten or skipped.

An EOF tail without LF is recoverable **only when its bytes are a possible
prefix of a valid record**. It is discarded at the last valid record offset,
and the truncation is fsynced before accepting requests. Even a complete-looking
record without its LF is uncommitted. Invalid complete records earlier in the
file prevent any tail repair. Keep backups before manual recovery.

### Durability boundaries

- Success means the OS accepted file fsync, not proof that faulty hardware,
  controller caches, network filesystems or virtual disks honor stable storage.
- If rollback itself fails, the log's on-disk outcome is uncertain; an errored
  operation might appear after restart. Inspect/repair storage before restarting.
- A connection can fail after persistence but before the client sees its reply.
  Such a command may have committed; there are no request IDs or exactly-once
  retry guarantees.
- The format has no checksum. Syntactically valid corruption cannot be detected,
  and prefix-only torn-tail recovery is not a general corruption repair tool.
- Do not externally edit, replace, rename or rotate the active AOF. The lock is
  advisory and protects cooperating processes, not hostile filesystem writers.
- No compaction, snapshots, replication, transactions spanning commands or
  distributed consensus. The AOF grows with write history; disk space and backup
  management remain operational responsibilities.

The store explicitly frees replaced/deleted keys and values, replay temporaries,
parsed arguments and responses. It does not depend on stdlib private layouts or
assume a garbage collector.

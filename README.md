# fin-kv

A single-process key-value store for the [Fin programming language](https://github.com/M1778/Fin):
a bounded in-memory table, an append-only log, and a loopback TCP line protocol.

All parsing, storage, recovery, and networking run in the compiled Fin binary. Python
in this repository is a test driver only.

- **Platform:** Linux x86-64 and ARM64. macOS and Windows builds compile to a named
  refusal, because the store needs Linux `flock`, `uname`, and the per-architecture
  `O_DIRECTORY`/`O_NOFOLLOW` bits. See [Platform support](#platform-support).
- **Verified with:** `finc 1.0.1 (contract 3)`, and the published `finc 1.0.0`
  release artifacts. The version under test is pinned in [`FINC_VERSION`](FINC_VERSION).
- **Not a Redis replacement.** No replication, cluster mode, authentication, TLS,
  compaction, or concurrency beyond one client at a time. It also is not durable
  against hardware lying about fsync; see [Durability boundaries](#durability-boundaries).

## What it does

| Command | Response |
|---|---|
| `PING [message]` | `PONG`, or the message |
| `SET key value` | `OK` |
| `SET key value EX seconds` | `OK` |
| `GET key` | the value, or `(nil)` |
| `DEL key [key ...]` | `(integer) N` |
| `EXISTS key` | `(integer) 0` or `(integer) 1` |
| `TTL key` | seconds left, `-1` without expiry, `-2` if missing |
| `KEYS *` | `(keys) N` then N key lines |
| `QUIT` | `OK`, then the connection closes |
| blank line | blank response line |
| anything else | `(error) message` |

Commands are case-insensitive, one per LF-terminated line; CRLF is accepted. Every
accepted `SET` and `DEL` is appended to `fin-kv.aof` and `fsync`ed before the reply.

## What it does not do

- No persistence format compatibility promise across releases beyond the documented
  `SET`/`DEL` record shapes. The log has no checksum.
- No expiry sweeper: expiry is lazy on reads, plus a full sweep on `KEYS *`. Expired
  entries can hold capacity until something visits them.
- No eviction. At capacity, writes are refused (`(error) store capacity exceeded`);
  nothing is silently dropped.
- No compaction, snapshots, replication, clustering, transactions across commands,
  authentication, or TLS.
- No `KEYS pattern` beyond `*`. No `SCAN`. No integers or lists: values are byte strings.
- No concurrent clients: one connection is served to completion before the next is
  accepted. An active client can therefore delay others.

## Install and build

The store is a binary, not a library. With `finc` on `PATH`:

```sh
finc src/main.fin -o fin-kv
```

With the package manager (`finn`):

```sh
finn build          # writes out/fin-kv
```

`finn.toml` names `main.fin` as the entrypoint, so `finn build` reads
`src/main.fin` without further configuration.

Run it in the directory that should hold the log:

```sh
mkdir data && cd data
/path/to/fin-kv
# fin-kv listening on 127.0.0.1:7379 (aof: fin-kv.aof)
```

Then, from another terminal:

```sh
printf 'PING\nSET greeting hello world\nGET greeting\nQUIT\n' | nc 127.0.0.1 7379
```

```output
PONG
OK
hello world
OK
```

A Fin client. The server is single-threaded, so this completes one round trip and
exits; a real client would loop over replies.

```fin
#[llvm_name="socket"]   @define sock(domain: int, kind: int, protocol: int) <int>;
#[llvm_name="connect"]  @define conn(fd: int, addr: &int, size: int) <int>;
#[llvm_name="inet_addr"] @define addr_of(host: string) <int>;
#[llvm_name="send"]     @define send_all(fd: int, buf: &char, n: ulong, flags: int) <long>;
#[llvm_name="recv"]     @define recv_some(fd: int, buf: &char, n: ulong, flags: int) <long>;
#[llvm_name="close"]    @define close_fd(fd: int) <int>;
import { len, to_chars, from_chars } from strings::std;

fun main() <int> {
    let port <int> = 7379;
    let fd <int> = sock(2, 1, 0);
    let sa <[int]> = new [int, 4]{};
    // sockaddr_in: sin_family = 2, sin_port and sin_addr in network byte order.
    sa[0] = cast<int>(2 + cast<long>(port / 256) * cast<long>(65536) + cast<long>(port % 256) * cast<long>(16777216));
    sa[1] = addr_of("127.0.0.1");
    blame conn(fd, &sa[0], 16) == 0, "connect failed";
    let text <string> = "PING\n";
    let request <[char]> = to_chars(text);
    let n <ulong> = cast<ulong>(len(text));
    blame send_all(fd, &request[0], n, 0) == cast<long>(n), "send failed";
    let reply <[char]> = new [char, 64]{};
    let got <long> = recv_some(fd, &reply[0], cast<ulong>(63), 0);
    blame got > cast<long>(0), "recv failed";
    printf("reply: %s", from_chars(reply, 0, cast<int>(got)));
    close_fd(fd); delete sa; delete request; delete reply;
    return 0;
}
```

```output
reply: PONG
```

`recv` is a single call: it can return fewer bytes than one reply, and it can return
`EAGAIN` while the server is still working. Production clients accumulate until a
newline arrives rather than assuming one `recv` is one reply.

## Configuration

| Variable | Values | Default |
|---|---|---|
| `FIN_KV_PORT` | decimal `0..65535`; `0` picks an ephemeral port and reports it | `7379` |
| `FIN_KV_TIMEOUT_MS` | decimal `1..60000`; per-frame and per-reply deadline | `5000` |

The listener binds `127.0.0.1` only. `fin-kv.aof` is created in the working
directory with mode `0600`; existing permissions are not changed, and symlinks are
refused. An exclusive `flock` refuses a second cooperating writer.

Loopback is not an authorization boundary against other local users. There is no
authentication: keep the data directory private and never put this behind a public
proxy without separate access control.

## Limits

Limits are checked before persistence or mutation, so a refused command changes
neither memory nor the log.

| Bound | Value | Behaviour past it |
|---|---|---|
| Frame, bytes before LF (a trailing CR counts) | 8192 | `(error) invalid or oversized frame`, connection closed |
| Key | 1..256 bytes, no whitespace or control bytes | `(error)` |
| Value | 1..4096 bytes; spaces are preserved | `(error)` |
| `DEL` keys per command | 128 | `(error)`; duplicates count once |
| Entries | 8192 | `(error) store capacity exceeded` |
| Key bytes + value bytes | 33554432 (32 MiB) | `(error) store capacity exceeded` |
| Expiry argument | `1..2147483647` seconds | `(error)` |

There is no eviction and no growth beyond these bounds: a full store refuses
writes until you `DEL` something. Each limit is tested at its edge and one step
past it in [`tests/boundary.py`](tests/boundary.py).

## Persistence

Records are `SET key absolute_epoch value\n` (`0` = no expiry) and
`DEL key [key ...]\n`. For every accepted write:

1. Validate syntax and capacity without touching the dataset.
2. Append the record; check the byte count.
3. `fflush`, then `fsync` the log descriptor.
4. Only then mutate memory and reply.

A failed write, flush, or sync leaves the command unapplied, truncates and
fsyncs back to the last committed offset, and puts the writer into a
fail-closed state **until restart** — later writes are refused even if the device
recovers. Reads continue.

Startup streams the log with fixed-size buffers, validating every record. It
exits nonzero, before listening, on an unreadable log, an invalid complete
record, or a capacity violation; nothing is skipped or rewritten. An EOF tail
without LF is discarded only when it is a possible prefix of a valid record, and
that truncation is fsynced before requests are accepted.

### Durability boundaries

- Success means the OS accepted `fsync`, not that hardware, controller caches,
  network filesystems or virtual disks honour stable storage.
- If rollback itself fails, the on-disk outcome is uncertain; an errored write
  might appear after restart. Inspect the file before restarting.
- A connection can fail after persistence but before the client reads the reply.
  That command may have committed. There are no request IDs or exactly-once retry.
- The format has no checksum, so syntactically valid corruption cannot be detected.
- Do not edit, replace, rename or rotate the active log. The lock is advisory and
  protects cooperating processes, not hostile filesystem writers.
- The log grows with write history and has no compaction. Disk space and backups
  remain operational responsibilities.

## Platform support

| Platform | Store | What CI runs |
|---|---|---|
| Linux x86-64, ARM64 | supported | builds and runs the full suite |
| macOS (either arch) | **not supported** | builds, then must print the named refusal and exit 1 |
| Windows (either arch) | **not supported** | builds, then must print the named refusal and exit 1 |

`src/main.fin` selects its whole body with `#c_ifdef FIN_TARGET_LINUX`. Off Linux,
the file compiles to a program that prints
`error: fin-kv requires Linux x86_64 or aarch64; this binary is a platform refusal, not a server`
and exits `1`. This is deliberate: the store's extern declarations (`uname`,
`flock`, `fdopen` with Linux `O_DIRECTORY`/`O_NOFOLLOW` bits) have no portable
equivalent in this repository, and the compiler emits every declared function, so
a partial build would fail to link rather than degrade.

The macOS and Windows CI lanes are not silent skips. They build the real source,
execute the binary, and require that exact message and a nonzero exit
(`tests/platform.py --expect refusal`). A build that stopped compiling, or a
refusal weakened into a zero exit, fails those lanes.

## Tests

```sh
python3 tests/run.py                                  # build and run what this host can
python3 tests/run.py --compiler /path/to/finc --opt O2
python3 tests/run.py --bin out/fin-kv                 # test an existing binary
```

| File | Covers | Requirements |
|---|---|---|
| `tests/platform.py` | server starts and persists, or the refusal names the platform | any platform |
| `tests/boundary.py` | every limit at its edge and one past; truncated and oversized frames; mid-write disconnect; RSS growth under overwrite churn | any platform; `/proc` for the growth check |
| `tests/smoke.py` | protocol, restart, expiry, corrupt/torn log recovery, 100k-record replay, capacity, fault-injected write/flush/fsync failures | Linux: `/proc`, `LD_PRELOAD`, FIFOs, `flock` |

`tests/smoke.py` builds a test-only `LD_PRELOAD` shim to route writes to
`/dev/full` and to fail `fflush`/`fsync`. It does not replace the storage engine:
the bytes still go to a real file descriptor.

## Public interface

Only three things are public: the wire protocol above, the two environment
variables, and the `fin-kv.aof` file format. There is no Fin library to import;
`src/main.fin` is a program with a `main`, not a module.

## License

MIT. See [LICENSE](LICENSE).

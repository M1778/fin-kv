# Changelog — fin-kv

All notable changes to fin-kv are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## Unreleased

### Added
- Line-protocol TCP server on 127.0.0.1:7379: `PING`, `SET` (values with
  spaces, optional `EX <seconds>`), `GET`, `DEL` (multi-key), `EXISTS`,
  `TTL`, `KEYS *`, `QUIT`.
- In-memory store on stdlib `HashMap` (values + expiry epochs in two maps);
  lazy expiry on every read path.
- Append-only log persistence (`fin-kv.aof`): every `SET`/`DEL` appended,
  replayed at startup, already-expired entries skipped.
- `finn.toml` in the real finn manifest format (`[project]`/`[scripts]`,
  from the finn repo README 2026-10-19).
- Python smoke test (`tests/smoke.py`): full protocol assertions over a real
  socket, expiry timing, and restart persistence via the AOF.
- GitHub Actions CI (install finc → check → build → smoke).

### Known limitations
- UNVERIFIED: no working `finc` in the build environment yet; code written
  against `docs/agent-guide.md` (Fin v0.4.0) and stdlib sources only.
- Single-threaded (one client at a time); transient per-command allocations
  are not reclaimed; AOF has no compaction; `KEYS` only supports `*`.

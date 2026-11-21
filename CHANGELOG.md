# Changelog — fin-kv

All notable changes to fin-kv are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Compile-time platform selection in `src/main.fin`: the store body is selected by
  `#c_ifdef FIN_TARGET_LINUX`, and every other target compiles to a program that
  names the requirement and exits `1`. Previously the POSIX externs were declared
  unconditionally, so off-Linux builds failed to link instead of refusing by name.
- `tests/platform.py`: asserts the host-correct outcome — a serving store on Linux,
  a named refusal plus exit `1` elsewhere — and can be pointed at either branch with
  `--expect`, so the refusal is exercised on Linux too.
- `tests/boundary.py`: every advertised limit at its edge and one byte past (frame
  8192, key 256, value 4096, `DEL` 128, 8192 keys, 32 MiB). Also truncated frames,
  oversized frames, a mid-write client disconnect, trickled frames, and an RSS check
  under repeated max-size overwrites.
- `tests/kv.py`: the process and socket harness, shared by all three drivers.
- `tests/run.py`: one command that builds and runs every test the host can execute.
- `FINC_VERSION`: the pinned compiler version CI installs.
- `.github/scripts/install_finc.sh`: fetches the pinned `finc` release, verifies the
  published SHA-256, and refuses a triple with no artifact.
- Three-platform CI (Linux x86-64 at O0 and O2, Linux ARM64, macOS, Windows) that
  installs a released compiler instead of building one from source, and
  compiles-and-runs a self-check program before trusting it.

### Changed

- CI no longer builds LLVM and `finc` from source: it uses the published release and
  verifies the archive checksum. This removes a 180-minute job for a repository that
  needs a compiler binary, not a compiler checkout.
- macOS and Windows lanes now build and execute the real `src/main.fin` and require
  the named refusal. They are explicit about being unsupported rather than green
  because nothing ran.
- `tests/smoke.py` no longer carries its own server harness; the shared one lives in
  `tests/kv.py`. Its architecture-mapping check reads the Linux branch of the
  guarded source rather than assuming the file has one `main`.
- `README.md` rewritten: verified compiler versions, install and run instructions, a
  compiled and executed Fin client example, the public interface, the POSIX/Windows
  split, and the durability boundaries. Removed the link to an unrelated CI run as
  proof of this repository's behaviour.

### Fixed

- `README.md` claimed Fin `0.4.0` and pointed macOS support at a non-existent
  configuration. Both now match what the repository builds and tests.
- CI linked with `finc`'s default `-lLLVM-22`, which no clean runner has, so
  every Linux lane failed at its first build. `FIN_LDFLAGS=-lm` now names the
  libraries the generated code actually needs.

### Removed

- The in-repository compiler build in CI (LLVM 22 + Conan + `./build.sh`). The
  release artifact is smaller, reproducible, and checksum-verified.

## Known limitations

- The store is Linux-only. macOS and Windows are refused by name at build time.
- One client at a time; a slow client delays the next connection until its deadline.
- No eviction: at capacity, writes are refused until a key is deleted.
- No AOF compaction, so the log grows with write history.
- No checksum on AOF records; syntactically valid corruption is undetectable.
- `KEYS` only supports `*`, and it sweeps expired entries while building the reply.

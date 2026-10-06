# KIU Enhancement Project — Agent Rules

## Exact supported build

- Kid Icarus: Uprising USA, TitleID `0004000000030100`, product `CTR-P-AKDE`, remaster 0.
- Source dump SHA256: `98a5fd7708d4d31364953f05183c919093c9a1bac9a0a1b7a97fbb33c063cbc8`.
- Code SHA256: `2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679`.
- Canonical public compatibility data: `manifests/title.json`. User extraction metadata belongs in `local/`.

## Hard rules

- Never patch or dispatch by guest address alone: verify expected bytes first.
- Record address, original/replacement bytes and build hash for every patch.
- Unknown/mixed patches must be refused, not overwritten.
- Never commit or publish game dumps, extracted code, assets, saves or derived game binaries.
- Generated files and private research belong in ignored `local/`; never zip the whole workspace for release.
- Preserve user changes. Keep superseded source and uncommitted source changes recoverable in Git branches/history, not duplicate archive folders or inventory snapshots. Private research stays ignored in its original `local/` paths; never commit game material to preserve it.
- Azahar baseline is the behavioral oracle. Find the first divergence, not just the visible symptom.
- No stubs solely to advance a trace. Fixes need deterministic regressions.
- Read actual tools before reporting status; source mtime outranks a document's self-reported date.
- Keep game hooks and mailbox ABI platform-neutral. Windows-only imports belong in lazy-loaded host adapters.
- ABI layout/semantics changes require a version bump, new fingerprints and compatibility tests.

## Current priorities

60 FPS and battle widescreen are accepted. Native mouse is a preview; timing races and savestate recovery remain.
Menu routing is experimental, not finished single-screen UI. The user requested an opt-in Launcher checkbox; keep it off by default. See `STATUS.md` for current state.
Do not resurrect old mouse paths or F9/framebuffer-copy routing as the public entry point.
Maintain current technical conclusions in `docs/KNOWLEDGE.md`; link operational/architecture documentation instead of duplicating superseded reports or personal work logs.

## Development

Use Python 3.11+ and the declared dependencies. Run `python -m unittest discover -s tests`.
Clean-checkout tests must not require a ROM; optional private-image checks remain available when `local/extracted/exefs_code.bin` exists.
The research workstation may use a local conda `kiu` environment; never hard-code its location in public tools.

Runtime code belongs in `src/kiu/`: platform-neutral core/protocol, `patches/`, lazy host `backends/`, and explicitly opt-in `experimental/`.
Development extraction/analysis tools belong in `tools/dev/`; run them as `python -m tools.dev.NAME`.
Keep `python run_kiu.py`, installed `kiu-enhance`, and `python -m kiu` as equivalent public CLI entry points.
The player entry point is `run_launcher.py` / installed `kiu-launcher`, packaged as a Windows onedir Launcher. Its session must verify the user-owned game before launch, bind to the launched Azahar PID, and restore only patches owned by that session. Never close the emulator or overwrite its configuration on Launcher exit.

`prepare_kiu.py` accepts a user-owned decrypted CCI. It verifies the exheader and ExeFS superblock hashes.
ExeFS entry offsets are bytes; the table has ten 0x10-byte entries. Per-file hashes on the known decrypted dump reflect ciphertext and do not match; do not present them as proof of corruption or proof of integrity.
Compressed ExeFS code is not supported by this verified workflow.

## Distribution

Project code is MIT; third-party libraries and Azahar retain their licenses. No Nintendo game material is licensed by this repository.
`codex/development` is for development; `main` is the public candidate. Keep private research branches/history local-only. When publication is authorized, push only the approved public branch; never use `git push --all` / `--mirror` or publish research branches/full `.git` directories.
Do not publish, push, create a GitHub release or alter emulator configuration without user authorization.

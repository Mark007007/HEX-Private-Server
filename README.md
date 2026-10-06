# HEX Private Server

Unified integration repository for **IanUtley/hex-server** and **RomoSJR/Dingler-FrostRingArena**.

The root repository is the integration/orchestration project. The upstream game
server remains the single authoritative implementation and is pinned as a Git
submodule under `hex-server/`. Dingler-FrostRingArena is pinned under
`upstream/Dingler-FrostRingArena/` so its original Arena/AI/Deck Import work can
be inspected and selectively integrated without introducing a second Rules
Engine.

## Layout

```text
HEX-Private-Server
├── hex-server/                         # upstream, pinned; sole game authority
├── upstream/Dingler-FrostRingArena/   # upstream, pinned; source/reference
├── integration/
│   ├── deck_import/                    # adapted DeckLink codec/import boundary
│   └── ai_bridge/                      # Original AI IPC boundary
├── legacy-ai-worker/                   # C# worker contract/scaffold
├── tests/                              # integration tests
├── docs/                               # merge notes and source map
├── scripts/
└── upstream/UPSTREAM.lock
```

## Pinned upstreams

- `IanUtley/hex-server` `main` @ `c65f2cf7e78797fb6d9da9a3401345da7cead71d`
- `RomoSJR/Dingler-FrostRingArena` `arena` @ `8c06748080ab3fd15d67a6b2f7193615ffd2db02`

Both upstream repositories expose AGPL-3.0 licensing. Keep the upstream
notices and Dingler's `THIRD-PARTY-NOTICES.md` when redistributing.

## Bootstrap

```bash
git clone --recurse-submodules https://github.com/Mark007007/HEX-Private-Server.git
cd HEX-Private-Server
bash scripts/pull_upstreams.sh
bash scripts/apply_integration.sh
python -m unittest discover -s tests -v
```

If the repository was cloned without submodules:

```bash
git submodule update --init --recursive
bash scripts/pull_upstreams.sh
```

## Architecture rule

`hex-server` remains the only source of truth for gameplay state, transactions,
persistence, and RulesPort validation. Dingler is used selectively for:

- original client-AI hosting behavior and compatibility knowledge;
- Hex Codex DeckLink parsing/import behavior;
- Frost Ring Arena reliability/regression knowledge.

An AI worker may return a decision, but the final decision must still be turned
into a typed transaction and validated/submitted by `hex-server`.

# HEX Private Server

**HEX-Private-Server** is the integration project that combines:

- **IanUtley/hex-server** — the single authoritative server, persistence layer, RulesPort, PVP/PVE and game-state engine.
- **RomoSJR/Dingler-FrostRingArena** — a pinned source/reference implementation whose original-client AI hosting, Hex Codex deck-link importer and Frost Ring Arena reliability fixes are selectively adapted.

The goal is a runnable private-server project, not a second competing rules engine.

## Repository layout

`text
HEX-Private-Server
├── hex-server/                         # pinned upstream, sole authoritative game server
├── upstream/Dingler-FrostRingArena/   # pinned Dingler arena branch
├── integration/
│   ├── deck_import/                    # Hex Codex v1 decoder + server persistence adapter
│   └── ai_bridge/                      # Original AI JSONL bridge + RulesPort transaction adapter
├── overlay/hex-server/                 # reviewed hex-server integration files
├── legacy-ai-worker/                   # headless Game.Shared AI host
├── tests/                              # integration/regression tests
├── scripts/
└── .github/workflows/ci.yml
`

## Fixed upstream versions

| Component | Repository | Ref |
|---|---|---|
| Authoritative server | IanUtley/hex-server | `main @ c65f2cf7e78797fb6d9da9a3401345da7cead71d` |
| Arena / original AI reference | RomoSJR/Dingler-FrostRingArena | `arena @ 8c06748080ab3fd15d67a6b2f7193615ffd2db02` |

Both upstream projects are AGPL-3.0. Keep the upstream license notices and Dingler's `THIRD-PARTY-NOTICES.md` when redistributing the combined source.

## Integration status

The integration is implemented as a **reviewed overlay on top of the pinned hex-server submodule**. Dingler's server engine is not copied into the authoritative runtime.

| Stage | Status | Implementation |
|---|---|---|
| 1. Deck Import | Done | Dingler-compatible v1 decoder, validation, ownership-aware instance allocation |
| 2. `/importdeck` | Done | Command wired before the developer-console gate |
| 3. Reserve | Done | Dedicated `decks.reserves` storage + reserve wire flag |
| 4. Codex Gem mapping | Done | `gems.json` type -> server `gem_templates.gem_type` |
| 5. Original AI worker | Implemented + CI-probed | Headless `ClientSessionBase` mirror + original AI transaction capture; CI loads the supplied client DLLs |
| 6. Arena AI hosting | Implemented | Dingler-style event routing, resync/livelock safety, authoritative submission boundary |
| 7. RulesPort transaction conversion | Done | Worker intents become typed `RulesTransaction` objects |
| 8. Python fallback | Done | Original AI failure falls through to existing Python AI |
| 9. Arena regression layer | Done | Existing hex-server Arena remains authoritative; focused integration tests cover the integration contracts |
| 10. Build/start/battle validation | CI pipeline ready | GitHub Actions builds the worker, validates all seven client DLLs on Windows, loads the original runtime, constructs an AI session mirror, and runs the server tests available without client-derived Records |

## 1. Deck Import

The project includes the Dingler-compatible Hex Codex v1 deck-link decoder:

- URL or bare-code input
- CRC32 verification
- unsigned-varint decoding
- main-deck and reserve sections
- named deck sections
- deterministic validation errors

The importer consumes the player's existing `card_instances`. It does not silently mint arbitrary cards beyond the player's collection.

## 2. `/importdeck`

Usage:

`text
/importdeck <Hex Codex deck link>
`

The command is handled before the developer-console session gate and reports the imported deck id plus main/reserve counts.

Configure the Hex Codex data files:

`bash
export HEX_CODEX_DATA=/path/to/hex-codex-data
`

The directory must contain:

`text
ids.json
gems.json
`

## 3. Reserve persistence

Reserve cards have their own `decks.reserves` JSON field.

They are not merged into the main deck. The profile encoder emits each reserve card with the real reserve flag expected by the client deck representation.

Existing databases are upgraded in place by the normal `static.py` column-ensure path.

## 4. Codex gem mapping

Codex gem IDs are resolved in two steps:

`text
Codex site gem id
        ↓
ids.json → gem GUID
        ↓
gems.json → game gem type name
        ↓
gem_templates
        ↓
numeric EGemTypesNew value
`

A missing mapping is a hard import error. A Codex site ID is never stored as if it were an `EGemTypesNew` enum number.

## 5. Original AI C# worker

The worker in `legacy-ai-worker/` is a headless runtime bridge for the original `Game.Shared.AI` implementation. The worker process is kept alive per server handler so the headless `ClientSessionBase` mirror can preserve decision state across requests.

At runtime it can:

1. load the user-provided `Assembly-CSharp-firstpass.dll`;
2. resolve `Game.Shared.AI.AIPlayer` / `AITactical`;
3. construct a dynamic `ClientSessionBase` mirror;
4. deserialize and route `SessionEventArgs`;
5. capture the original AI's typed transaction;
6. project the transaction into the integration decision schema.

Configure:

`text
HEX_CLIENT_DLL=/path/to/Assembly-CSharp-firstpass.dll
HEX_ORIGINAL_AI=1
`

The seven user-supplied HEX client runtime DLLs are staged by CI into the same layout used by Dingler: the three compile-time references go under `upstream/Dingler-FrostRingArena/DLLs/`, and all seven runtime DLLs go under `upstream/Dingler-FrostRingArena/Dingler.Terminal/bin/Release/net10.0/`. The worker does not require them at compile time; at runtime it resolves sibling managed DLLs from the same directory as `Assembly-CSharp-firstpass.dll`.

## 6. Arena AI hosting

The Original AI path follows the useful parts of Dingler's Arena hosting model:

- live event routing into an AI mirror;
- AI-side decision capture;
- repeated-move suppression;
- stall detection / resync;
- livelock detection;
- void-on-broken-AI behavior rather than awarding a strike;
- authoritative submission back through hex-server.

The Dingler `HexRulesEngine` is **not** used as a second authority.

## 7. RulesPort transaction boundary

The C# worker returns only an intent, for example:

`json
{"kind":"play_troop","payload":{"card_id":123}}
`

The integration layer maps that intent to a typed `RulesTransaction`.

The transaction then goes through:

`text
Original AI
   ↓
decision intent
   ↓
RulesTransaction
   ↓
RulesPort.submit_transaction()
   ↓
authoritative validation
   ↓
RulesPort.handle_transaction()
   ↓
state/event projection
`

Supported transaction families include priority pass, resource/card play, ability activation, discard, attack, defense and activation-data continuations.

## 8. Python AI fallback

Original AI is optional.

A disabled, missing, malformed, timed-out or rejected Original AI path falls back to the existing Python AI path. The fallback never bypasses RulesPort when the native lifecycle is active.

The safety policy tracks:

- 15-second AI stall timeout;
- up to 3 resync attempts;
- repeated transaction suppression;
- 5000-move livelock detection per phase key.

## 9. Arena regression layer

The authoritative Arena implementation stays in hex-server.

Dingler Arena source is used for targeted compatibility knowledge rather than copied wholesale. The integration tests exercise:

- DeckLink parsing;
- main/reserve separation;
- gem mapping failure behavior;
- worker protocol;
- typed decision conversion;
- fallback behavior;
- livelock/repeated-signature protection.

When the client-derived `Records/*.jsonl` snapshot is available, the CI workflow also runs the upstream server encoding/commands/Arena regression suites.

## 10. Build and validation

### Local

`bash
git clone --recurse-submodules https://github.com/Mark007007/HEX-Private-Server.git
cd HEX-Private-Server

bash scripts/pull_upstreams.sh
bash scripts/apply_integration.sh

python -m unittest discover -s tests -v
dotnet build legacy-ai-worker/LegacyAiWorker.csproj -c Release --nologo
`

Run the server using `hex-server/HOWTO.md`.

### GitHub Actions

`.github/workflows/ci.yml` now:

1. checks out both pinned submodules;
2. verifies both exact upstream commit hashes;
3. applies the integration overlay;
4. runs all integration tests;
5. runs Python syntax checks;
6. runs server regression tests when client-derived Records are present;
7. builds the C# Original-AI worker on `windows-latest`;
8. verifies all seven uploaded client DLLs are non-empty;
9. performs the generic worker JSONL health smoke test;
10. launches the worker with `Assembly-CSharp-firstpass.dll`, requires `status=ready`, then constructs a real AI session mirror with `action=probe`.

## Current verification

In the current development sandbox:

`text
Python integration tests: 9/9 PASS
Python syntax checks: PASS
C# build: delegated to GitHub Actions
Original client runtime health: exercised by the Windows CI pipeline from the Dingler runtime layout
Original AI session construction probe: included in the Windows CI pipeline
Dingler reference build: included in the Windows CI pipeline
`

A full Original-AI end-to-end battle still requires a valid game/session event stream and matching client-derived `Records/*.jsonl` data. The uploaded DLLs remove the previous binary-availability blocker; they do not manufacture missing game-state records.

## Architecture rule

`text
                    HEX Private Server
                           │
                           ▼
                    hex-server RulesPort
                           │
             ┌─────────────┴─────────────┐
             │                           │
        Player transaction          AI decision
                                         │
                                  Original AI worker
                                         │
                                  typed intent only
                                         │
                                         ▼
                              RulesPort validation
                                         │
                                         ▼
                                  authoritative state
`

**Dingler is an integration/reference source, not a second server authority.**


## Client DLLs

The repository contains the seven HEX client managed DLLs supplied for this integration:

- `Assembly-CSharp-firstpass.dll`
- `ICSharpCode.SharpZipLib.dll`
- `NCalc.dll`
- `SampleClassLibrary.dll`
- `System.EnterpriseServices.dll`
- `System.Web.Services.dll`
- `UnityEngine.dll`

They are deployment inputs from the user-owned client installation. Anyone redistributing this repository should separately verify that redistribution is permitted by the applicable HEX/Unity/client terms; the repository's AGPL license does not grant third-party rights to proprietary client binaries.

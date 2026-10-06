# HEX Private Server

Unified integration repository for IanUtley/hex-server and RomoSJR/Dingler-FrostRingArena.

## Architecture

hex-server is the only authoritative gameplay/rules/persistence implementation. Dingler is pinned as a source/reference tree and its Arena/AI/Deck Import work is integrated selectively.

```text
HEX-Private-Server
├── hex-server/                         # pinned hex-server upstream
├── upstream/Dingler-FrostRingArena/   # pinned Dingler arena branch
├── integration/                        # runtime integration modules
├── overlay/hex-server/                # reviewed changes applied to hex-server
├── legacy-ai-worker/                  # real Game.Shared.AI runtime boundary
├── tests/
└── scripts/
```

## Pinned upstreams

- IanUtley/hex-server @ c65f2cf7e78797fb6d9da9a3401345da7cead71d
- RomoSJR/Dingler-FrostRingArena branch arena @ 8c06748080ab3fd15d67a6b2f7193615ffd2db02

Both upstreams are AGPL-3.0. Dingler's THIRD-PARTY-NOTICES.md must remain available when redistributing.

## Build

```bash
git clone --recurse-submodules https://github.com/Mark007007/HEX-Private-Server.git
cd HEX-Private-Server
bash scripts/pull_upstreams.sh
bash scripts/apply_integration.sh
python -m unittest discover -s tests -v
```

Run the upstream server according to hex-server/HOWTO.md.

## Deck import

Set HEX_CODEX_DATA to a Hex Codex data folder containing ids.json and gems.json, then send `/importdeck <link>` in HEX chat.
Imported cards use owned card_instances. Main and reserve cards remain separate, and Codex gem GUIDs are resolved through the server gem_templates table to EGemTypesNew numeric values.

## Original AI

Set HEX_ORIGINAL_AI=1, HEX_CLIENT_DLL to your own HEX Assembly-CSharp-firstpass.dll, and HEX_ORIGINAL_AI_PLUGIN to an adapter exposing public string Decide(string requestJson).

The worker never accepts a snapshot as state. It returns only an intent; hex-server converts that intent into a RulesTransaction and validates/submits it through RulesPort. If the worker is unavailable, invalid, or times out, the existing Python AI remains the fallback.
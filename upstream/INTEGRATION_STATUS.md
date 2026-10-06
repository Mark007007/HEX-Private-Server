# Integration status

Repository: Mark007007/HEX-Private-Server

Upstream commits:
- hex-server: c65f2cf7e78797fb6d9da9a3401345da7cead71d
- Dingler Frost Ring Arena (arena): 8c06748080ab3fd15d67a6b2f7193615ffd2db02

Completed integration layers:
- DeckLink v1 decode/validation
- collection-backed card instance allocation
- separate reserve persistence and wire encoding
- Codex gem GUID -> server EGemTypesNew mapping
- /importdeck command hook
- AI intent -> RulesTransaction boundary
- Original AI worker runtime boundary
- Python AI fallback
- focused integration tests and CI

External runtime dependency:
the Dingler repository intentionally does not contain the HEX client Assembly-CSharp-firstpass.dll. Original Game.Shared.AI execution therefore requires the user's own game DLL and an adapter implementation; the server never fabricates an original-AI result when those are absent.
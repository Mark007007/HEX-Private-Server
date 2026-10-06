# Original AI Worker

This worker is the runtime boundary for the original Game.Shared AI.

Dingler does not ship the HEX client binaries. Provide your own Assembly-CSharp-firstpass.dll via HEX_CLIENT_DLL and an adapter assembly via HEX_ORIGINAL_AI_PLUGIN.

The adapter must expose:

    public string Decide(string requestJson)

The returned JSON object contains an intent such as:

    {"kind":"pass","payload":{}}

Only the intent crosses into Python. The hex-server RulesPort validates and commits it. A missing DLL, missing adapter, malformed result, timeout, or rejected transaction causes the normal Python AI path to continue.
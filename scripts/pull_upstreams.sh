#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
git submodule sync --recursive
git submodule update --init --recursive
git -C hex-server fetch --depth=1 origin c65f2cf7e78797fb6d9da9a3401345da7cead71d
git -C upstream/Dingler-FrostRingArena fetch --depth=1 origin 8c06748080ab3fd15d67a6b2f7193615ffd2db02
git -C hex-server checkout --detach c65f2cf7e78797fb6d9da9a3401345da7cead71d
git -C upstream/Dingler-FrostRingArena checkout --detach 8c06748080ab3fd15d67a6b2f7193615ffd2db02
echo "hex-server: $(git -C hex-server rev-parse HEAD)"
echo "Dingler-FrostRingArena: $(git -C upstream/Dingler-FrostRingArena rev-parse HEAD)"

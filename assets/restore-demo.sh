#!/usr/bin/env bash
# Rebuild the demo video from its base64 parts (the binary is stored as text in
# the repo so it can be committed through the GitHub contents API).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
cat video/part-*.b64 | tr -d '\n' | base64 -d > wasel-demo.mp4
echo "✔ assets/wasel-demo.mp4 ($(du -h wasel-demo.mp4 | cut -f1))"

#!/usr/bin/env bash
# Rebuild the demo video from its base64 copy (the binary is stored in the repo
# as text so it can be committed through the GitHub contents API).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
base64 -d wasel-demo.mp4.b64 > wasel-demo.mp4
echo "✔ assets/wasel-demo.mp4 ($(du -h wasel-demo.mp4 | cut -f1))"

#!/usr/bin/env bash
# Downloads a pinned gitleaks release into .tools/bin and verifies its SHA-256.
set -euo pipefail
VERSION="${1:-8.30.1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/.tools/bin/gitleaks"
if [[ -x "$BIN" ]] && "$BIN" version | grep -q "$VERSION"; then exit 0; fi
OS=$(uname -s | tr '[:upper:]' '[:lower:]'); ARCH=$(uname -m)
[[ "$ARCH" == "x86_64" ]] && ARCH=x64
[[ "$ARCH" == "aarch64" ]] && ARCH=arm64
FILE="gitleaks_${VERSION}_${OS}_${ARCH}.tar.gz"
URL="https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
curl -sSfL -o "$TMP/$FILE" "$URL/$FILE"
curl -sSfL -o "$TMP/sums.txt" "$URL/gitleaks_${VERSION}_checksums.txt"
EXPECTED=$(grep " $FILE\$" "$TMP/sums.txt" | awk '{print $1}')
ACTUAL=$(shasum -a 256 "$TMP/$FILE" | awk '{print $1}')
[[ "$EXPECTED" == "$ACTUAL" ]] || { echo "checksum mismatch for $FILE" >&2; exit 1; }
mkdir -p "$ROOT/.tools/bin" && tar -xzf "$TMP/$FILE" -C "$TMP" gitleaks && mv "$TMP/gitleaks" "$BIN"
echo "gitleaks $VERSION installed to $BIN"

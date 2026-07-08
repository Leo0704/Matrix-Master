#!/usr/bin/env bash
# Sign an HTTP request the way the master controller does, and curl it.
#
# Usage:
#   ./scripts/sign-hmac.sh POST http://127.0.0.1:8765/xhs/publish '{"title":"hi"}'
#
# Requires: openssl, curl, jq. Pair-once SECRET env var holds the base64
# HMAC secret downloaded from the master during pairing.

set -euo pipefail

METHOD="${1:-GET}"
URL="${2:-http://127.0.0.1:8765/device/status}"
BODY="${3:-}"

if [[ -z "${SECRET_B64:-}" ]]; then
  echo "ERROR: SECRET_B64 is not set. Run the master pair once to receive it." >&2
  exit 1
fi

TS="$(date +%s)"
RID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
BODY_SHA="$(printf '%s' "$BODY" | shasum -a 256 | awk '{print $1}')"
CANONICAL="${TS}
${RID}
${BODY_SHA}"

SECRET_RAW="$(printf '%s' "$SECRET_B64" | base64 -d | xxd -p -c 256)"
SIG="$(printf '%s' "$CANONICAL" | openssl dgst -sha256 -mac HMAC -macopt hexkey:"${SECRET_RAW}" -binary | base64)"

curl -sS -X "$METHOD" "$URL" \
  -H "Content-Type: application/json" \
  -H "X-Timestamp: $TS" \
  -H "X-Signature: $SIG" \
  -H "X-Request-Id: $RID" \
  --data "$BODY" \
  -w "\n[HTTP %{http_code}]\n"

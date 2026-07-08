#!/usr/bin/env bash
# Convenience loop: rebuild, install, watch logs, restart service.
#
#   SERIAL=<adb-serial> ./scripts/dev-loop.sh
#
# Defaults to the only connected device if SERIAL omitted.

set -euo pipefail

SERIAL="${SERIAL:-$(adb devices | awk 'NR>1 && $2=="device" {print $1; exit}')}"
if [[ -z "$SERIAL" ]]; then
  echo "no adb device; connect a phone or start an emulator" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

( cd "$ROOT" && ./gradlew :app:assembleDebug )
adb -s "$SERIAL" install -r "$ROOT/app/build/outputs/apk/debug/app-debug.apk"
adb -s "$SERIAL" shell am force-stop com.matrix.companion.debug
adb -s "$SERIAL" shell am start -n com.matrix.companion.debug/com.matrix.companion.MainActivity
adb -s "$SERIAL" logcat -c
adb -s "$SERIAL" logcat -v threadtime MatrixCompanion:V '*:S'

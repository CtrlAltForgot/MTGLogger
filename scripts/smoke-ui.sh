#!/usr/bin/env bash
set -euo pipefail

mtglogger_url="${MTGLOGGER_URL:-http://127.0.0.1:5173}"
browser_bin="${BROWSER_BIN:-}"
if [[ -z "$browser_bin" ]]; then
  for candidate in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$candidate" >/dev/null 2>&1; then
      browser_bin=$(command -v "$candidate")
      break
    fi
  done
fi
if [[ -z "$browser_bin" ]]; then
  echo "Chrome or Chromium is required (or set BROWSER_BIN)." >&2
  exit 1
fi

validation_dir=$(mktemp -d /tmp/mtglogger-ui-validation.XXXXXX)
cleanup_ui() {
  case "$validation_dir" in
    /tmp/mtglogger-ui-validation.*) rm -rf -- "$validation_dir" ;;
  esac
}
trap cleanup_ui EXIT

upload_probe="$validation_dir/2mb-upload-probe.bin"
truncate -s 2M "$upload_probe"
upload_response="$validation_dir/upload-response.json"
upload_status=$(curl --silent --output "$upload_response" --write-out '%{http_code}' \
  -F "image=@$upload_probe" "${mtglogger_url%/}/api/scanner/upload-check")
if [[ "$upload_status" != "200" ]] || ! grep -q '"bytes":2097152' "$upload_response"; then
  echo "2 MB proxy upload expected a complete API response, received HTTP $upload_status" >&2
  exit 1
fi
printf 'proxy upload limit: API consumed complete 2 MB request\n'

for asset in manifest.webmanifest service-worker.js mtglogger-192.png mtglogger-512.png; do
  asset_status=$(curl --silent --output "$validation_dir/$asset" --write-out '%{http_code}' \
    "${mtglogger_url%/}/$asset")
  if [[ "$asset_status" != "200" || ! -s "$validation_dir/$asset" ]]; then
    echo "desktop app asset $asset expected HTTP 200 with content, received HTTP $asset_status" >&2
    exit 1
  fi
done
grep -q '"name": "MTGLogger"' "$validation_dir/manifest.webmanifest"
grep -q "CACHE_NAME = 'mtglogger-shell" "$validation_dir/service-worker.js"
printf 'desktop install assets: manifest, service worker, and icons available\n'

while IFS='|' read -r page expected; do
  profile="$validation_dir/profile-$page"
  dom="$validation_dir/$page.html"
  log="$validation_dir/$page.log"
  timeout 30 "$browser_bin" \
    --headless=new \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --user-data-dir="$profile" \
    --virtual-time-budget=5000 \
    --dump-dom "${mtglogger_url%/}/?page=$page" > "$dom" 2> "$log"
  if ! grep -Fq "$expected" "$dom"; then
    echo "$page did not render the expected text: $expected" >&2
    exit 1
  fi
  if grep -Eiq 'Uncaught|TypeError|ReferenceError|Minified React error' "$log"; then
    echo "$page emitted a browser runtime error:" >&2
    grep -Ei 'Uncaught|TypeError|ReferenceError|Minified React error' "$log" >&2
    exit 1
  fi
  if [[ "$page" == "scanner" && ! -d "$profile/Default/Service Worker" ]]; then
    echo "scanner loaded, but Chrome did not register the desktop app service worker" >&2
    exit 1
  fi
  printf '%s rendered: %s\n' "$page" "$expected"
done <<'EOF'
dashboard|Collection overview
scanner|Batch defaults
collection|Collection
decks|Decks
review|Review queue
sealed|Sealed inventory
EOF

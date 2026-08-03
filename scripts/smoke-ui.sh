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
upload_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -F "image=@$upload_probe" "${mtglogger_url%/}/api/upload-limit-validation")
if [[ "$upload_status" != "404" ]]; then
  echo "2 MB proxy upload expected API 404, received HTTP $upload_status" >&2
  exit 1
fi
printf 'proxy upload limit: 2 MB request reached API\n'

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
  printf '%s rendered: %s\n' "$page" "$expected"
done <<'EOF'
dashboard|Collection overview
scanner|Batch defaults
collection|Collection
decks|Decks
review|Review queue
sealed|Sealed inventory
EOF

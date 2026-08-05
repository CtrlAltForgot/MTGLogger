#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"
bundle_dir=${1:?Usage: import-reference-bundle.sh BUNDLE_DIR [DESCRIPTOR_DIR]}
descriptor_dir=${2:-/mnt/user/mtglogger-reference-data/descriptors}
test -f "$bundle_dir/reference-data.dump"
test -f "$bundle_dir/SHA256SUMS"

(
  cd "$bundle_dir"
  sha256sum --check SHA256SUMS
)

compose=(docker compose -f "$project_dir/docker-compose.yml")
if [[ -f docker-compose.unraid.yml ]]; then
  compose+=( -f "$project_dir/docker-compose.unraid.yml" )
fi

existing=$("${compose[@]}" exec -T db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select count(*) from card_references"' | tr -d '\r')
if [[ "$existing" != "0" ]]; then
  echo "Refusing to import over $existing existing references. Use the normal resumable updater." >&2
  exit 1
fi
mkdir -p "$descriptor_dir"
if [[ -n "$(find "$descriptor_dir" -mindepth 1 -print -quit)" ]]; then
  echo "Refusing to import into non-empty descriptor directory: $descriptor_dir" >&2
  exit 1
fi

"${compose[@]}" stop web api
restore_app=true
trap 'if [[ "$restore_app" == true ]]; then "${compose[@]}" up -d api web; fi' EXIT

cat "$bundle_dir"/descriptors.tar.part-* | tar -C "$descriptor_dir" -xf -
"${compose[@]}" exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --no-owner --exit-on-error' \
  < "$bundle_dir/reference-data.dump"

"${compose[@]}" up -d api web
restore_app=false
trap - EXIT
echo "Reference catalog imported. Open Database to verify coverage."

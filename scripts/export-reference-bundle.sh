#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"
output_dir=${1:?Usage: export-reference-bundle.sh OUTPUT_DIR [DESCRIPTOR_DIR]}
descriptor_dir=${2:-/mnt/user/mtglogger-reference-data/descriptors}
mkdir -p "$output_dir"

test -d "$descriptor_dir"
test -z "$(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit)"

compose=(docker compose -f "$project_dir/docker-compose.yml")
if [[ -f docker-compose.unraid.yml ]]; then
  compose+=( -f "$project_dir/docker-compose.unraid.yml" )
fi

"${compose[@]}" exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --data-only --strict-names --table=card_references --table=card_visual_fingerprints' \
  > "$output_dir/reference-data.dump"

catalog_counts=$("${compose[@]}" exec -T db sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select (select count(*) from card_references) || chr(124) || (select count(*) from card_visual_fingerprints)"' | tr -d '\r')
reference_count=${catalog_counts%%|*}
fingerprint_count=${catalog_counts##*|}
tar -C "$descriptor_dir" -cf - . | split -b 1900m - "$output_dir/descriptors.tar.part-"
(
  cd "$output_dir"
  sha256sum reference-data.dump descriptors.tar.part-* > SHA256SUMS
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'references=%s\n' "$reference_count"
  printf 'fingerprints=%s\n' "$fingerprint_count"
  printf 'descriptor_files=%s\n' "$(find "$descriptor_dir" -type f | wc -l | tr -d ' ')"
) > "$output_dir/MANIFEST"

echo "Reference bundle created in $output_dir"

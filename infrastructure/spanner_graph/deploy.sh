#!/usr/bin/env bash
# Apply the Spanner Graph DDL (one statement per call; DDL file uses ';' separators).
set -euo pipefail
: "${SPANNER_INSTANCE:?Set SPANNER_INSTANCE}"
: "${SPANNER_DATABASE:?Set SPANNER_DATABASE}"
gcloud spanner databases ddl update "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" \
  --ddl-file="$(dirname "$0")/schema.ddl"

"""Index the circulars already archived in Cloud Storage so the assistant can search their text.

    python scripts/index_circulars.py            # index what is missing
    python scripts/index_circulars.py --force    # re-embed everything
    python scripts/index_circulars.py --stats

Run it where the app database is reachable (a Cloud Run job with the API image and service account, since Cloud SQL is
private-IP only). New uploads are indexed automatically during analysis; this is only for the backfill.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import circular_index  # noqa: E402
from core.config import get_settings  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-embed documents that are already indexed")
    parser.add_argument("--stats", action="store_true", help="print what is indexed and exit")
    args = parser.parse_args()
    if args.stats:
        print(circular_index.stats())
        return 0
    settings = get_settings()
    if not circular_index.enabled():
        print("Embeddings are off (set the GCP backends, or CIRCULAR_INDEX=1 with an embedder).", file=sys.stderr)
        return 2
    from google.cloud import storage

    bucket = storage.Client(project=settings.project).bucket(settings.circulars_bucket)
    done = skipped = failed = 0
    for blob in bucket.list_blobs(prefix="archive/"):
        if blob.name.endswith("/"):
            continue
        name = Path(blob.name).name
        try:
            document = parse_document(blob.download_as_bytes(), name, f"gs://{settings.circulars_bucket}/{blob.name}")
            count = circular_index.index_document(document, force=args.force)
            done, skipped = done + bool(count), skipped + (not count)
            print(f"{'indexed' if count else 'skipped'}: {blob.name} ({count} chunks)")
        except Exception as error:  # noqa: BLE001 - one bad file must not stop the backfill
            failed += 1
            print(f"failed: {blob.name}: {type(error).__name__}: {error}", file=sys.stderr)
    print(f"done: {done} indexed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

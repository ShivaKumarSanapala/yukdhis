"""Run the deterministic pipeline (same stages as the ADK agents) and print the impact report summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app_store import get_app_store  # noqa: E402
from pipeline.deterministic import run_pipeline  # noqa: E402

DEFAULT = ROOT / "data" / "circulars" / "rbi_2023_09_13_release_of_property_documents.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic bank compliance impact demo (no LLM).")
    parser.add_argument("--circular", type=Path, default=DEFAULT)
    parser.add_argument("--as-of", help="ISO analysis date (default: data manifest reference date)")
    parser.add_argument("--output", type=Path, help="Write the full report JSON here")
    args = parser.parse_args()
    state = run_pipeline({"path": str(args.circular.resolve()), "filename": args.circular.name}, args.as_of,
                         lambda stage, kind, payload: kind == "stage_started" and print(f"[{stage}] {payload['label']}"))
    report = get_app_store().get_report(state["report_id"])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()

"""Score rule extraction against the gold set: the template-of-record extractions of the sample circulars.

Without an LLM (AGENT_MODE=deterministic or no GOOGLE_CLOUD_PROJECT) the candidate is the template itself, so every
score is 1.0: a smoke test of the scorer. With an LLM the candidate is a fresh Gemini extraction of each circular.

    python scripts/eval_extraction.py            # auto: Gemini when configured
    python scripts/eval_extraction.py --no-llm   # template versus template
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion import extractor  # noqa: E402
from ingestion.documents import parse_document  # noqa: E402
from ingestion.evaluation import score_extraction  # noqa: E402
from ingestion.templates import match_template  # noqa: E402

CIRCULARS = ROOT / "data" / "circulars"


def evaluate(paths: list[Path], use_llm: bool) -> list[dict]:
    rows = []
    for path in paths:
        document = parse_document(path.read_bytes(), path.name)
        template = match_template(document)
        if template is None:
            rows.append({"circular": path.name, "error": "no template of record (no gold set)"})
            continue
        gold = template(document)
        try:
            candidate = extractor._gemini_extract(document, "") if use_llm else gold
        except Exception as error:  # report, do not abort the other samples
            rows.append({"circular": path.name, "error": f"Gemini extraction failed: {error}"})
            continue
        rows.append({"circular": path.name, "candidate": "gemini" if use_llm else "template",
                     **score_extraction(candidate, gold)})
    return rows


def _line(row: dict) -> str:
    if "error" in row:
        return f"{row['circular']}: {row['error']}"
    r, c, f = row["rules"], row["checks"], row["fields"]
    fmt = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
    return (f"{row['circular']} [{row['candidate']}]\n"
            f"  rules   P {r['precision']:.2f}  R {r['recall']:.2f}  F1 {r['f1']:.2f}\n"
            f"  checks  P {c['precision']:.2f}  R {c['recall']:.2f}  F1 {c['f1']:.2f}\n"
            f"  fields  modality {fmt(f['modality'])}  effective_date {fmt(f['effective_date'])}  "
            f"capability {fmt(f['capability'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Precision/recall of rule extraction against the template gold set.")
    parser.add_argument("--no-llm", action="store_true", help="Score the template against itself")
    parser.add_argument("--json", action="store_true", help="Print the full scores as JSON")
    parser.add_argument("circulars", nargs="*", type=Path, help="Default: the template-backed samples in data/circulars (the gold set)")
    args = parser.parse_args(argv)
    use_llm = extractor.llm_enabled() and not args.no_llm
    rows = evaluate(args.circulars or sorted(CIRCULARS.glob("rbi_[0-9]*.md")), use_llm)
    print(json.dumps(rows, indent=2) if args.json else "\n".join(_line(r) for r in rows))
    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())

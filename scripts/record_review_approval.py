from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from podcaster.review import VALID_DECISIONS, apply_review_decision


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a Podcaster human review decision in an episode manifest.")
    parser.add_argument("--manifest", required=True, type=Path, help="Path to the existing episode manifest JSON.")
    parser.add_argument("--output", required=True, type=Path, help="Path to write the reviewed manifest JSON.")
    parser.add_argument("--reviewer", required=True, help="GitHub actor or reviewer identity.")
    parser.add_argument("--decision", required=True, choices=sorted(VALID_DECISIONS))
    parser.add_argument("--notes", default="")
    parser.add_argument("--reviewed-at", default=None, help="ISO 8601 UTC timestamp. Defaults to now.")
    parser.add_argument("--run-url", default=None)
    args = parser.parse_args()

    reviewed_at = args.reviewed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    updated = apply_review_decision(
        manifest,
        reviewer=args.reviewer,
        reviewed_at=reviewed_at,
        decision=args.decision,
        notes=args.notes,
        run_url=args.run_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(updated, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Re-arm a fenced provider retry after an authoritative absence proof.

Thin local wrapper around ``python -m podcaster.provider_retry_rearm`` (the
container image ships only the ``podcaster`` package, so run the module form
in-boundary). See ``docs/youtube-publish-workflow.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from podcaster.provider_retry_rearm import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

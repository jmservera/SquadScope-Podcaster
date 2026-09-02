# Image Attribution

## Claracle Logo (DOG watermark)

`claracle.jpeg` — the Claracle show logo, used as the DOG (Digital On-Screen
Graphic) watermark overlaid on the main content of generated videos.

Copyright © jmservera. All rights reserved.

Project-owned artwork, vendored from the sibling
[SquadScope](https://github.com/jmservera/SquadScope) repository
(`assets/images/claracle.jpeg`) so the synthesis container can brand every
episode without a network fetch. No third-party attribution is required.

The asset is bundled deliberately rather than fetched from a mutable public URL:
a remote-only watermark silently degraded to an unbranded video when the URL
stopped resolving (W36 regression, see `podcaster/watermark.py`).

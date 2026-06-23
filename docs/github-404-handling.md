# GitHub 404 / login-required repo handling (issue #386)

## Background

During video recording, some GitHub repos that the crawler successfully read
from would render a **404** (or a login wall) when opened in the headless
browser, producing a jarring "Repository unavailable" screen for viewers.

## Why the crawler succeeds but the browser 404s

The crawler and the recording browser hit GitHub differently, and several
GitHub behaviours explain the mismatch:

1. **Authentication context.** The crawler typically reads repo metadata via the
   GitHub **API** (often with a token) or from cached/article content. The
   recording browser loads `github.com/owner/repo` **unauthenticated**. Private
   or org-restricted repos return real data to an authenticated API token but
   redirect an anonymous browser to `github.com/login?return_to=…` (HTTP 200) or
   show a 404.

2. **Repo went private / was renamed / deleted after crawl.** The article that
   seeds an episode can be hours or days old. A repo that was public when
   crawled may have been made private, renamed, or removed by recording time —
   the API/cached data still exists, the live page doesn't.

3. **Rate limiting / bot mitigation.** Anonymous browser traffic to github.com
   is more aggressively throttled than tokened API traffic. Under load GitHub
   may serve an interstitial or a transient error to the browser while the API
   path the crawler used stays healthy.

4. **Slow load mistaken for failure.** A genuinely slow page can trip a
   navigation timeout, which looks like a hard failure even though the repo is
   fine.

## What we do about it

Implemented in `podcaster/video/video_gen.py`:

- **Distinguish slow vs. broken vs. login-required.**
  - Transient slowness is handled by the existing retry-with-backoff recovery
    (`_navigate_with_recovery`, issues #378/#381).
  - A hard HTTP `>= 400` (e.g. 404) fails the navigation immediately.
  - A 200 that lands on `github.com/login` or `…/session` is detected by
    `_is_login_redirect()` and treated as a failure rather than recording the
    login page.

- **Try the project website before giving up.** When the repo page can't be
  recorded, `_try_record_project_site()` probes the repo's conventional GitHub
  Pages site (`https://{owner}.github.io/{name}/`) and, if it loads, records
  that real content instead. The recording is tagged `recovery_path="website"`.

- **Clean URL card instead of an error screen.** If no website is available, we
  render a branded **URL card** (`_render_url_card`) showing the repo identity
  and `github.com/owner/repo` prominently. There is no "unavailable" wording —
  the segment reads as intentional content.

## Recovery order

```
direct → retry (backoff) → article (corrected URL) → website (GitHub Pages) → URL card
```

"""Natural, human-spoken project names for GitHub repos (issue #627).

The hosts should *say* a repo's real product name — not read its raw
``owner/repo`` slug aloud with the org prefix, slashes, dashes and underscores
spelled out ("jmservera slash SquadScope dash Podcaster"). This module derives a
spoken/display name, in priority order:

1. The repo's ``README.md`` main title (first level-1 ``#`` H1).
2. Fallback: the repo name *after* the ``/`` (the org/user prefix is dropped).
3. Naturalize the chosen name: ``-``/``_`` -> space, collapse whitespace, trim.

README content is treated as **untrusted**: the extracted title is control-char
stripped, Markdown decoration is removed, injection-flagged titles are rejected
(falling back to the slug), and the result is hard-capped. A natural name can
therefore never introduce a new line, marker, or instruction into the spoken
script or any downstream prompt.

The raw ``owner/repo`` slug remains the canonical identifier for links, dedup,
and video repo-window lookups — this module only changes how a repo is
*spoken/shown*.
"""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlparse

from podcaster.sanitization import _strip_control_chars, flag_injection

#: A fetcher that returns the raw README text for ``(owner, name)`` or ``None``.
ReadmeFetcher = Callable[[str, str], "str | None"]

# Spoken names are short by nature; cap so a padded README title can never
# dominate a line or a prompt.
_MAX_SPOKEN_NAME_CHARS = 60
# Upper bound on README bytes read over the network (READMEs are small; this
# bounds attacker-controlled bandwidth).
_MAX_README_BYTES = 200_000
# Cap repos processed per script so a link-stuffed article can't fan out into an
# unbounded number of network fetches.
_MAX_REPOS = 16
_FETCH_TIMEOUT = 3.0

# Full ``https://github.com/owner/repo`` URL (mirrors the lenient repo regexes
# used elsewhere so a trailing period yields a clean slug).
_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]*[A-Za-z0-9_-])",
)

# Valid GitHub owner/repo path segment (used to guard the network fetch).
_VALID_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

# Fenced-code delimiters (``` or ~~~), up to three leading spaces.
_FENCE_RE = re.compile(r"^\s{0,3}(```+|~~~+)")
# ATX H1: up to three leading spaces, a single ``#`` (not ``##``), then space(s).
_ATX_H1_RE = re.compile(r"^ {0,3}#(?!#)\s+(.*)$")
_WHITESPACE_RE = re.compile(r"\s+")

# A cleaned title that still looks like a URL / repo host reference is rejected:
# left intact it would be spoken robotically and — for a ``github.com`` URL —
# harvested downstream as a spurious new repo reference (defeating the invariant
# that an untrusted README can never introduce a new canonical repo).
_URLISH_RE = re.compile(r"https?://|www\.|github\.com|githubusercontent\.com", re.IGNORECASE)

# Full-URL span within a line — left untouched by the spoken-name rewrite so all
# URL-based harvesting (visual markers, section repo slugs, video windows) keeps
# working off the canonical slug.
_URL_SPAN_RE = re.compile(r"https?://\S+")
# A bare ``owner/repo`` slug not glued to a surrounding token and not part of a
# longer path (no trailing ``/`` segment).
_BARE_SLUG_RE = re.compile(
    r"(?<![\w./@-])"
    r"([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?![\w/-])",
)


def naturalize_name(name: str) -> str:
    """Replace ``-``/``_`` with spaces, collapse whitespace, trim.

    Control/zero-width characters are stripped first (untrusted input) and
    Unicode is NFKC-normalized. Legitimate branding is preserved: capitalization
    is kept and CamelCase is never split.
    """
    text = _strip_control_chars(name or "")
    text = text.replace("-", " ").replace("_", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _hard_cap(text: str, limit: int) -> str:
    """Truncate at a word boundary without appending any marker."""
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    if " " in clipped:
        clipped = clipped[: clipped.rfind(" ")]
    return clipped.strip()


def _strip_markdown_decoration(text: str) -> str:
    """Strip Markdown/HTML decoration, keeping only visible text."""
    # Images / badges: drop entirely.
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    # Inline links ``[text](url)`` -> ``text``.
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Reference links ``[text][ref]`` -> ``text``.
    text = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", text)
    # Bare bracketed text ``[text]`` -> ``text``.
    text = re.sub(r"\[([^\]]*)\]", r"\1", text)
    # HTML tags.
    text = re.sub(r"<[^>]+>", " ", text)
    # Inline code backticks and emphasis markers.
    text = text.replace("`", "")
    text = re.sub(r"[*]{1,3}", "", text)
    return text


def _trim_edges(text: str) -> str:
    """Trim leading/trailing non-word characters (emoji, punctuation)."""
    return re.sub(r"^[^\w]+|[^\w]+$", "", text, flags=re.UNICODE).strip()


def extract_readme_title(readme_text: "str | None") -> "str | None":
    """Return a sanitized first-H1 project title, or ``None``.

    Robust to a missing README, no H1, H1s inside fenced code blocks, and
    trailing closing ``#`` runs. README text is untrusted: an injection-flagged
    title is rejected (caller falls back to the slug), decoration is stripped,
    and the result is control-char cleaned and hard-capped.
    """
    if not readme_text:
        return None

    in_fence = False
    fence_marker = ""
    for raw in readme_text.splitlines():
        fence = _FENCE_RE.match(raw)
        if fence:
            token = fence.group(1)[0] * 3  # normalize to ``` or ~~~ family
            if not in_fence:
                in_fence, fence_marker = True, token
            elif token == fence_marker:
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue

        heading = _ATX_H1_RE.match(raw)
        if heading is None:
            continue

        # NFKC + strip control/zero-width chars before any parsing.
        content = _strip_control_chars(heading.group(1))
        # Drop a trailing closing ``#`` run (``# Title #``).
        content = re.sub(r"\s+#+\s*$", "", content).strip()
        if not content:
            continue
        # Untrusted: never let an instruction-like title through.
        if flag_injection(content):
            return None
        cleaned = _strip_markdown_decoration(content)
        cleaned = _trim_edges(cleaned)
        cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
        if not cleaned:
            continue
        # Reject a title that still carries a URL / repo host reference so an
        # untrusted README can never inject a spoken URL or a harvestable repo.
        if _URLISH_RE.search(cleaned):
            continue
        return _hard_cap(cleaned, _MAX_SPOKEN_NAME_CHARS) or None
    return None


def repo_name_from_slug(slug: str) -> str:
    """Return the repo name after the ``/`` (org/user prefix stripped).

    Accepts ``owner/repo``, a full GitHub URL, or a bare name. ``.git`` suffixes
    and trailing dots are removed.
    """
    text = (slug or "").strip()
    if "//" in text or text.lower().startswith("github.com"):
        parsed = urlparse(text if "//" in text else "https://" + text)
        segments = [seg for seg in parsed.path.split("/") if seg]
    else:
        segments = [seg for seg in text.split("/") if seg]
    if not segments:
        return ""
    name = segments[1] if len(segments) >= 2 else segments[0]
    if name.lower().endswith(".git"):
        name = name[:-4]
    return name.rstrip(".")


def spoken_repo_name(slug: str, *, readme_text: "str | None" = None) -> str:
    """Resolve the spoken/display name for a repo (README H1 -> name -> naturalize)."""
    title = extract_readme_title(readme_text)
    if title:
        natural = naturalize_name(title)
        if natural:
            return natural
    return naturalize_name(repo_name_from_slug(slug))


def fetch_readme(
    owner: str,
    name: str,
    *,
    timeout: float = _FETCH_TIMEOUT,
    max_bytes: int = _MAX_README_BYTES,
) -> "str | None":
    """Best-effort fetch of a repo's README from ``raw.githubusercontent.com``.

    Uses the ``HEAD`` ref so the repo's default branch is honored. The host is
    fixed and the ``owner``/``name`` segments are strictly validated, so there is
    no SSRF surface. Redirects are disabled and the body is size-capped. Any
    failure (network error, non-200, oversize) returns ``None`` — README
    preference is an enhancement, never a hard dependency.
    """
    if not _VALID_SEGMENT_RE.match(owner or "") or not _VALID_SEGMENT_RE.match(name or ""):
        return None

    import requests

    url = f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/README.md"
    try:
        with requests.get(url, timeout=timeout, allow_redirects=False, stream=True) as resp:
            if resp.status_code != 200:
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
            return b"".join(chunks)[:max_bytes].decode("utf-8", errors="replace")
    except Exception:
        return None


def _harvest_repos_from_urls(text: str) -> list[tuple[str, str]]:
    """Unique ``(owner, repo)`` pairs from every full GitHub URL in *text*."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for owner, repo in _GITHUB_URL_RE.findall(text or ""):
        repo = repo[:-4] if repo.lower().endswith(".git") else repo
        key = (owner.lower(), repo.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append((owner, repo))
    return out


def build_spoken_name_map(
    text: str,
    *,
    fetch: "ReadmeFetcher | None" = None,
) -> dict[tuple[str, str], str]:
    """Map ``(owner_lower, repo_lower)`` -> spoken name for repos named by URL.

    Only repos that appear as a full ``github.com/owner/repo`` URL are included
    so the canonical slug is always retained for links/lookups. README fetching
    (when *fetch* is supplied) is best-effort per repo and never fatal.
    """
    mapping: dict[tuple[str, str], str] = {}
    for owner, repo in _harvest_repos_from_urls(text)[:_MAX_REPOS]:
        readme: "str | None" = None
        if fetch is not None:
            try:
                readme = fetch(owner, repo)
            except Exception:
                readme = None
        mapping[(owner.lower(), repo.lower())] = spoken_repo_name(
            f"{owner}/{repo}", readme_text=readme
        )
    return mapping


def _replace_bare_slugs(text: str, name_map: dict[tuple[str, str], str]) -> str:
    """Replace known bare ``owner/repo`` slugs in URL-free *text* with their name."""

    def _sub(match: "re.Match[str]") -> str:
        owner, repo = match.group(1), match.group(2)
        repo_clean = repo[:-4] if repo.lower().endswith(".git") else repo
        key = (owner.lower(), repo_clean.lower())
        name = name_map.get(key)
        return name if name else match.group(0)

    return _BARE_SLUG_RE.sub(_sub, text)


def _rewrite_line(line: str, name_map: dict[tuple[str, str], str]) -> str:
    """Rewrite bare slugs in a spoken line, leaving any full URL spans intact."""
    parts: list[str] = []
    last = 0
    for match in _URL_SPAN_RE.finditer(line):
        parts.append(_replace_bare_slugs(line[last : match.start()], name_map))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_replace_bare_slugs(line[last:], name_map))
    return "".join(parts)


def rewrite_spoken_repo_names(
    dialogue: str,
    name_map: dict[tuple[str, str], str],
) -> str:
    """Replace robotic bare ``owner/repo`` slugs in spoken lines with natural names.

    Only spoken dialogue is rewritten: ``##`` marker/section lines and full
    ``https://`` URLs are left untouched so visual markers, ``Repos featured``
    metadata, section repo slugs, and video repo-window lookups keep resolving
    off the canonical slug. Because the substituted name is control-char cleaned
    and single-line, it can never inject a new marker or script line.
    """
    if not dialogue or not name_map:
        return dialogue
    out: list[str] = []
    for raw in dialogue.splitlines():
        if raw.lstrip().startswith("#"):
            out.append(raw)
            continue
        out.append(_rewrite_line(raw, name_map))
    result = "\n".join(out)
    if dialogue.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result

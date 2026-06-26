# Per-language Spotify show publishing (#438)

Spotify treats **language as a show-level property**, so each language is
published to its **own** Spotify show rather than mixing languages in one feed
(mixed-language feeds hurt discovery and violate Spotify's catalog
expectations).

| Language | Show name        | Show-level language tag | Show-id source                    |
| -------- | ---------------- | ----------------------- | --------------------------------- |
| en       | Claracle Weekly  | `en`                    | `SPOTIFY_SHOW_ID`                 |
| es       | Claracle Semanal | `es`                    | `SPOTIFY_SHOW_ID_ES`             |
| fr       | Claracle Hebdo   | `fr`                    | `SPOTIFY_SHOW_ID_FR`             |

## One-time operator setup (per language)

For each non-English language:

1. In Spotify for Creators, **create a new show** for the language.
2. Set the show's **primary language** to the correct tag (`es`, `fr`, …). This
   is set on the show, *not* per episode — there is no per-episode language
   field in the upload API.
3. Fill show-level metadata (title, description, cover art) in the target
   language.
4. Copy the new show's id into the corresponding environment variable
   (`SPOTIFY_SHOW_ID_ES`, `SPOTIFY_SHOW_ID_FR`, …).

The browser cookies `SP_DC` / `SP_KEY` are account-level and shared across all
shows owned by the same creator account — they do **not** need to be duplicated
per language.

## How resolution works (code)

`podcaster/spotify_shows.py` resolves the show target for a language, in order:

1. `language_config.spotify_show_id` — explicit per-language config (#432).
2. `SPOTIFY_SHOW_ID_<LANG>` environment variable (English uses `SPOTIFY_SHOW_ID`).

There is **no cross-language fallback** — if `SPOTIFY_SHOW_ID_ES` is absent
the show id stays empty and `_get_credentials()` raises with the exact variable
name to set.  This prevents silently publishing Spanish/French episodes into the
English show.

```python
from podcaster.spotify_shows import resolve_show_target
target = resolve_show_target("es", language_config=lang_cfg)
# -> ShowTarget(language="es", show_id=..., language_tag="es", show_name="Claracle Semanal")
```

`publish.publish_episode(..., language="es", language_config=lang_cfg)` and
`publish.verify_spotify_auth("es")` route to the right show. English callers
need no changes — `language` defaults to `"en"` and reads `SPOTIFY_SHOW_ID`.

## Config-driven targets (#432)

Show ids and language tags can also come from the per-language config object
(duck-typed attributes `spotify_show_id`, `spotify_language_tag`, `show_name`,
`locale`), so deployments may keep show ids in config/secrets rather than env
vars. Config values take precedence over environment variables.

## Acceptance mapping

- ✅ Each locale publishes to the correct show (resolved per language).
- ✅ English behavior unchanged (`SPOTIFY_SHOW_ID`, default `language="en"`).
- ✅ Show targets are config-driven (#432), not hard-coded.
- ✅ Show-level language tag tracked per show (`ShowTarget.language_tag`).

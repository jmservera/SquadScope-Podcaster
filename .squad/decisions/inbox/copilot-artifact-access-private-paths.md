# Artifact access uses private operator paths

## Decision

Podcaster generated artifact URLs use the `private_operator_path` access model for the initial release. Response URLs are storage/local locator paths only; they must not include SAS tokens, URL credentials, query strings, or fragments.

## Rationale

Placeholder podcast artifacts are not publishable output, and public/SAS access is unnecessary before human review, real TTS, and publication gates exist. Keeping returned URLs private avoids accidental public exposure while still giving operators stable artifact locators for validation.

## Impact

- Operators need local filesystem access in development or explicitly granted Azure Storage permissions in deployed environments.
- Job manifests and publishing packets include `artifact_access` metadata for URL policy, expiry, cleanup ownership, audit correlation, and publication blockers.
- Cleanup is driven by `expires_at`/`cleanup_after` and owned by an operator or storage lifecycle policy.
- Future SAS or brokered access requires an explicit follow-up decision and tests.

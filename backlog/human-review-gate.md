# Backlog: Human Review Gate

All production (non-dry-run) podcast synthesis requires **mandatory human editorial review** before audio is finalized. This ensures accuracy, compliance, and quality standards are met.

## Mandatory Editorial Checks

Before any non-dry-run TTS synthesis, reviewers MUST verify all items in **section 6 (Review Gate Constraints)** of `docs/editorial-standards.md`:

1. **Script accuracy**
   - All claims in the claim ledger marked `verified: true`
   - No unspoken markup or unresolved editorial notes in script body
   - Script reads naturally when spoken aloud (editor performs audio test)
   - All acronyms expanded; no orphan abbreviations

2. **Claim verification**
   - Spot-check at least 3 major claims against source article
   - Inferences or derived claims have editor sign-off with justification
   - No contradictions with source material

3. **Citation and link integrity**
   - All URLs in show notes resolve (HTTP 200 or 3xx with live final target)
   - Transcripts/show-notes metadata complete (title, episode, source URL, date, duration)

4. **Transcript readiness**
   - Format matches approved standard (plain-text + timestamps or SRT)
   - Speaker IDs and tone cues correct
   - No profanity, slurs, or sensitive content without explicit context flag

5. **TTS readiness**
   - Script validated by selected TTS provider (dry-run synthesis or lint check)
   - No provider-specific unsupported characters in script
   - Provider license permits intended distribution

6. **Dry-run handling**
   - If `dry_run: true`: manifest created with `status: dry_run`, stub transcript/show notes generated, no audio synthesis
   - Response includes warnings for any non-fatal issues

---

## Functional Requirements

- MVP review mechanism: use the GitHub Environment named `podcast-review` in `.github/workflows/podcast-review-gate.yml`. Maintainers configure required reviewers on that environment; the workflow pauses before recording any approval decision.
- A job transitions from `accepted`/`review_pending` (auto-generated) to `review_approved`, `changes_requested`, or `rejected`.
- Reviewers can request script changes, new voice, or audio regeneration.
- Only `approved` jobs should be eligible for manual publishing to Spotify/podcast hosts.
- If script, TTS provider, or voice selection changes, audio must be invalidated and regenerated with new review.
- Non-dry-run TTS synthesis remains blocked until the review manifest records `decision: approved`; dry-run/non-publishing checks may run without approval.

---

## Reviewer Identity & Audit Trail

All review actions must record:

- **Reviewer identity:** `github.actor` (GitHub username) from authenticated context.
- **Timestamp:** ISO 8601 UTC (e.g., `2026-06-07T19:07:49.816+00:00`).
- **Job reference:** Job ID, week, article URL (non-sensitive).
- **Decision:** `approve`, `request_changes`, `regenerate`, `reject`.
- **Reason/comment:** Optional free-text explanation.
- **Artifact hash (optional):** SHA-256 of transcript or audio to track which version was reviewed.

**Approval signature:** Approved reviews are signed or cryptographically bound to prevent tampering. Audit trail is retained in manifest and publishing packet for legal/compliance purposes.

### Storage options

- **Option 1:** GitHub issue comments (simple, tied to PR, public).
- **Option 2:** JSON files committed to a `reviews/` branch (immutable, audit-friendly).
- **Option 3:** Azure Table Storage (scalable, queryable, indexed by job ID).

---

## Security & Operational Requirements

### Authentication & Authorization

- Reviewers must be authenticated GitHub users with write permission to the Podcaster repository.
- Review actions are recorded in the repository (via commits, pull requests, or issues) or in Azure storage with GitHub actor attribution.
- No reviewer credentials (PATs, keys) are stored in review records.

### Artifact Integrity & Regeneration

When a reviewer requests changes (e.g., "use British accent"), the job transitions to `rejected`:

- Old SAS URLs expire immediately (or are removed from the manifest).
- Podcaster generates new audio and stages new artifacts.
- A new job ID is issued, or the same job ID is reused with incremented revision (e.g., `podcast-2026-W23-abc12345-v2`).
- SquadScope requests regeneration by calling `/api/generate` with `force: true` and optional `notes` field.
- Regenerated artifacts require full review cycle before approval.

### Secrets in Review

**Critical:** Review comments, diffs, and logs must NOT contain:
- API keys (none should be in transit; validate during response checks)
- SAS URLs with embedded credentials (return read-only SAS URLs only, never connection strings)
- Caller credentials or tokens
- Storage account keys or connection strings

**Safe to review:**
- Transcript text (article summary)
- Speaker/voice metadata (e.g., "narrator: female-en-us")
- Audio duration and bitrate
- Title, description, and show notes

### Rate Limiting & Feedback

- Reviewers should approve/reject a job in under 1 minute of interaction.
- Rejection or regeneration request is delivered to SquadScope via callback URL (future feature, see `integration-contract.md`).
- If no callback is configured, SquadScope polls job status periodically (e.g., every 5 minutes).

---

## Implementation Steps

1. **Add review status to job record:**
   - Add `review_status` field: `pending`, `approved`, `rejected`, `changes_requested`.
   - Add `reviewer_id` (GitHub username) and `review_timestamp`.
   - Add `review_notes` (optional comment).
   - Include the selected review mechanism (`github_environment`, `podcast-review`) and the artifacts a reviewer must inspect.

2. **Implement reviewer interface:**
   - Trigger `.github/workflows/podcast-review-gate.yml` with the job ID, private manifest URL, private publishing packet URL, decision, and non-secret notes.
   - GitHub pauses the job at environment `podcast-review` until an authorized reviewer approves it.
   - The workflow uploads `review-manifest.json` as an Actions artifact for reviewer/operator audit.

3. **Implement audit trail:**
   - Log each review action to Application Insights with reviewer, timestamp, job ID, and decision.
   - Optionally commit review records to a `reviews/` branch or Azure storage.

4. **Implement regeneration workflow:**
   - When a job is rejected, update artifact URLs to null or mark as expired.
   - Validation ensures regenerated jobs pass the same checks as originals.
   - SquadScope rerequests with `force: true` and receives a new job ID.

5. **Implement callback (optional for v1):**
   - Store callback URL from the request.
   - After review decision, invoke the callback with job status (approved/rejected).
   - SquadScope can webhook-subscribe instead of polling.

6. **Testing:**
   - Unit test: Verify review status transitions are valid (e.g., can't approve a rejected job).
   - Integration test: Simulate reviewer approval workflow via GitHub comments.
   - Security test: Verify no secrets leak into review logs or artifacts.

---

## Compliance Notes

- ✓ **No stored credentials:** Review process uses GitHub authentication; no PATs or keys stored in Podcaster.
- ✓ **Audit trail:** Immutable record of who approved what and when.
- ✓ **Artifact integrity:** Regenerated artifacts are isolated; old URLs expire.
- ✓ **Least privilege:** Reviewers need write access to the repo, not admin access.
- ✓ **Editorial standards:** All review decisions tied to mandatory checks in `docs/editorial-standards.md`.

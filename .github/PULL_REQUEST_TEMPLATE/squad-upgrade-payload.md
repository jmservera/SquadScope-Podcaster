## Summary

<!-- Platform-only Squad upgrade payload changes -->

Closes #22

## Readiness gate

- [ ] Podcaster deploy/product dependency chain is green and ready to merge.
- [ ] This PR is a dedicated platform branch/PR for Squad upgrade payload work.

## Scope isolation

- [ ] No deploy/product files are included in this PR.
- [ ] Only platform/workflow/config payload files are included.

## Upgrade scope inventory

List every touched area (or `none`):

- Workflows:
- Templates:
- Skills:
- MCP config:
- Memory/Casting files:
- Other platform-only files:

## Validation (platform-only)

- [ ] Ran applicable workflow/config validation that does not require product deploy gates.
- Commands/results:
  - `<command>` — `<result>`

## Required reviews

- [ ] Bender review requested for workflow/platform mechanics.
- [ ] Hermes review requested if permissions, secrets, MCP, or automation access changed.

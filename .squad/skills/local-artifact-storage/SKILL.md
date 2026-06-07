---
name: local-artifact-storage
description: Use filesystem-backed artifact staging as the dev fallback for Azure Blob production paths
domain: azure-functions, storage, local-dev
confidence: medium
source: earned (Podcaster production-path increment)
---

## Pattern

When adding Azure Blob-backed generation features, define a small storage protocol and provide a local filesystem implementation selected when Azure storage settings are absent.

## Context

Use this for Azure Functions that must run tests and local validation without cloud credentials while still exercising the production artifact lifecycle.

## Guidance

- Keep the response contract independent of the backend.
- Write deterministic artifact paths under `jobs/<job_id>/`.
- Store lifecycle/review details in the manifest instead of adding top-level API response fields.
- Use managed identity for Azure Blob writes; do not add key-based app settings for artifact storage.

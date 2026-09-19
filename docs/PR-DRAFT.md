# Consolidate profile isolation, reliable reflection and incremental memory capture

Memory-OS installations accumulated fixes outside the public repository. This candidate combines the profile and local-embedding contributions with the production fixes, adding regression coverage for failures that previously looked like successful memory operations.

Invalid LLM responses now produce an explicit unanalyzed result instead of increasing confidence or creating raw reflection blobs. Missing profile databases cannot fall back to another agent's sessions. Ingestion waits for worker confirmation. Icarus capture is isolated by session and idempotent per turn; native configuration can be staged without installing services.

Contributions: Brian Doherty (PR #37), plasmaStray (PR #35), the Principal/Grok/Hermes Principal fixes in `86ed776`, and the Lucidus recovery adapters. PR #31's actual patch is already represented in the base; the YAML normalization described by its title is implemented separately here.

Validation: offline regression runner, existing collapse/sanitization/profile/provider suites, real embedded-Qdrant indexing with synthetic vectors, shell syntax and Compose parsing. No live migration or paid-provider test. Review docs/CONSOLIDATION.md and docs/MIGRATION.md before release.

This text is a local draft. No PR was created.

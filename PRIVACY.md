# Privacy

This repository and its image must contain only deterministic synthetic fixtures. Never commit account exports, account identifiers, credentials, cookies, tokens, personal notes, screenshots, transcripts, live databases, or deployment-specific paths.

The first remote repository and GHCR package must be private for owner review. Review the full Git history, Actions history and logs, image metadata, both target platforms, every OCI layer and label, and current scan results before a separate public-visibility approval. If provenance is uncertain, create a new clean release repository with one controlled initial commit. Public visibility is the final one-way gate: forks, caches, clones, and pulled images can survive deletion, and a public GHCR package cannot be made private again.

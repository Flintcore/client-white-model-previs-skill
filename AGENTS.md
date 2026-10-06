# Repository changes

This repository publishes the skill, paraphrased standards, reusable checks and isolated tests; it does not publish client source documents, videos, models, credentials, generated production evidence or machine-local paths.

- Preserve complete rule source mappings, struck-out dispositions and project-local decisions. A new client version requires a new standards version/hash, migration review and release; do not weaken gates to make a fixture green.
- Production jobs pin a full Git revision and rules digest. Never equate rule coverage, machine tests, sample fixtures or queue accepted with client visual acceptance.
- Run standard-library tests, skill validation and any changed helper. Changes to Blender checks require actual isolated Blender positive/negative cases; inspect saved-file hashes before/after.
- Keep machine setup configurable. Back up installed non-Git skills before update and verify byte hashes; do not write secrets or production artifacts into Git.
- Use codex/ prefixed working branches for subsequent changes. No automatic pushes, client submissions or destructive cleanup unless the current human request includes them.

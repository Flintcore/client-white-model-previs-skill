# Repository changes

This repository publishes the skill, standards, checks and isolated tests. The user explicitly requested public client models/examples on 2026-10-07: publish only the originals enumerated in client-materials/manifest.json (small files bundled, large videos in the pinned Release). Never expand that list to production attempts, credentials, private path indexes or unrelated personal files.

- Preserve complete rule source mappings, struck-out dispositions and project-local decisions. A new client version requires a new standards version/hash, migration review and release; do not weaken gates to make a fixture green.
- Production jobs pin a full Git revision and rules digest. Never equate rule coverage, machine tests, sample fixtures or queue accepted with client visual acceptance.
- Run standard-library tests, skill validation and any changed helper. Changes to Blender checks require actual isolated Blender positive/negative cases; inspect saved-file hashes before/after.
- Keep machine setup configurable. Back up installed non-Git skills before update and verify byte hashes; do not write secrets or production artifacts into Git.
- Use codex/ prefixed working branches for subsequent changes. No automatic pushes, client submissions or destructive cleanup unless the current human request includes them.
- Keep the root/bundled asset manifest and tools/installed helper byte-identical; preserve original asset SHA and distinguish reference availability from current acceptance. Rules remain the immutable 1.0.0 snapshot; its old publication wording is historical, not the current material locator.

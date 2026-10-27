# Security model

- Provider credentials are loaded by the backend from environment variables and excluded from provider listing responses.
- Configure `EZEAGENT_WORKSPACE` to a dedicated workspace. File paths are resolved and checked against that root; symlink entries are omitted from listings.
- Text reads and patch targets are capped at 1 MB; proposed content is capped at 500 KB.
- Patch approval is a separate API action. A proposal records the original file SHA-256 and expiry, and the backend checks both status and current file content before applying.
- This release does not provide authentication. Run only on a trusted machine and do not expose the API to untrusted networks.
- No arbitrary shell execution or automated remote writes are exposed.
- Treat model output and repository contents as untrusted. Review all diffs before approval.

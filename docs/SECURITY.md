# Security model

- Provider credentials are loaded by the backend from environment variables and excluded from provider listing responses.
- Configure `EZEAGENT_WORKSPACE` to a dedicated workspace. File paths are resolved and checked against that root; symlink entries are omitted from listings.
- Text reads and patch targets are capped at 1 MB; proposed content is capped at 500 KB.
- Patch approval is a separate API action. A proposal records the original file SHA-256 and expiry, and the backend checks both status and current file content before applying.
- This release does not provide authentication. Run only on a trusted machine and do not expose the API to untrusted networks.
- The local dashboard can open a visible Windows Command Prompt and run a reviewed command after the user sees the exact text and confirms each run. Commands run as the current Windows user; they can modify or delete that user's files and settings. Proposals stay in server memory and audit records omit command text and output.
- Computer-control endpoints require a loopback client and an allowed dashboard Origin. Keep the backend bound to localhost and do not expose it to untrusted networks.
- Treat model output and repository contents as untrusted. Review all diffs before approval.


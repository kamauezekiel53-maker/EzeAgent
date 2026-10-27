# Security model

- Provider credentials are loaded by the backend from environment variables and excluded from provider listing responses.
- Configure `EZEAGENT_WORKSPACE` to a dedicated workspace. File paths are resolved and checked against that root; symlink entries are omitted from listings.
- Text reads and patch targets are capped at 1 MB; proposed content is capped at 500 KB.
- Patch approval is a separate API action. A proposal records the original file SHA-256 and expiry, and the backend checks both status and current file content before applying.
- This release does not provide authentication. Run only on a trusted machine and do not expose the API to untrusted networks.
- The local dashboard can launch Word or Excel, open a visible Windows Command Prompt, and run a reviewed command after the user sees the exact text and confirms each run. Commands run as the current Windows user; they can modify or delete that user's files and settings. Proposals stay in server memory and audit records omit command text and output.
- Computer-control endpoints require a loopback client and an allowed dashboard Origin. Keep the backend bound to localhost and do not expose it to untrusted networks.
- Treat model output and repository contents as untrusted. Review all diffs before approval.



## EzeAgent Scope assessment workspace

- Assessment endpoints require a loopback client and the configured local dashboard Origin. This release has no user accounts or team roles; actor and approver names are self-reported.
- Creating an assessment requires a user attestation that written authorization exists and records its reference, owner, environment, dates, objectives, and rules of engagement. EzeAgent does not verify the underlying authorization document.
- Scope entries are explicit hostnames, IP addresses, or CIDR ranges. Hostnames match exactly by default; subdomains match only when separately enabled by the operator. Scope checks read stored records only and never contact a target.
- The assessment workspace has no active scan, exploit, or target-execution endpoint. It stores manually entered findings and evidence from material supplied by the user.
- Common credential patterns in finding evidence are masked before storage, but pattern matching is incomplete. Review and redact all evidence yourself. Do not store secrets, personal records, or evidence you are not authorized to retain.
- Assessment data uses the local SQLite database, which is not encrypted at rest by EzeAgent. Each assessment records a manual deletion date; the app does not automatically delete expired data. Deletion requires typing the assessment name. A minimal tombstone with a hashed assessment identifier, operator-entered actor name, and deletion time remains after content is removed.
- Generated reports contain the recorded authorization reference and findings. Review the report before sharing it.

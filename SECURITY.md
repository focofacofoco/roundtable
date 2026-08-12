# Security invariants

Roundtable delegates only to official provider CLIs already authenticated by their own login flows.

- API-key authentication and direct provider HTTP adapters are out of scope and prohibited.
- Credential files and raw authentication output must not be read, copied, logged, or persisted.
- Provider subprocesses receive a scrubbed environment and disposable working directory.
- Research fails closed unless local files, commands and MCP tools are denied while web access is explicitly allowed.
- Runs are ephemeral unless the caller explicitly supplies `--out` or `--save`.

Report vulnerabilities privately to the repository owner. Do not include credentials or raw auth-store contents.

[한국어](./SECURITY.md)

# Security guidance

This educational sample handles untrusted model-generated code. Add authentication, authorization, isolation, budget, and egress policies for service or multi-tenant use. Treat generated tool inputs and outputs as untrusted; do not execute them directly on the host. Limit execution permissions, resources, output, time, and tool calls. Generate hard-to-guess identifiers and validate each user’s ownership.

Grant Microsoft Entra roles with least privilege at supported scopes for the relevant Foundry resource, session pool, and sandbox group. Keep tokens and credentials on the host. Memory snapshots can contain secrets. Revalidate external connections, locks, and tokens after resume, and store any data needed after session expiry separately.

Keep `.env`, execution manifests, raw results, and internal materials private. In addition to using `.gitignore`, run `scripts/check-public-readiness.ps1 -RequireDocs` before publication and inspect staged files, history, binaries, and embedded image data. Revoke and rotate exposed secrets as appropriate. Clean up sample resources and remaining snapshots and volumes without deleting existing Foundry or other users’ resources.

Do not post secrets or exploit details in public issues. Report vulnerabilities through the repository's private security advisory feature if enabled, or otherwise contact maintainers through a private channel. Provide reproduction and impact without credentials.

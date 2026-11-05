[한국어](./SECURITY.md)

# Security guidance

This is an educational sample that handles untrusted model-generated code. It does not complete the authentication, authorization, isolation, budget, or egress policies needed for a service or multi-tenant deployment. Treat generated tool inputs and outputs as untrusted; do not execute them directly on the host. Limit execution permissions, resources, output, time, and tool calls. Generate hard-to-guess identifiers and validate each user's ownership.

Grant Microsoft Entra roles with least privilege at supported scopes for the relevant Foundry resource, session pool, and sandbox group. Keep tokens and credentials on the host and do not send them to sandboxes. Memory snapshots can contain secrets. Revalidate external connections, locks, and tokens after resume. Do not mistake idle session state loss for durable recovery.

Do not publish `.env`, execution manifests, raw results, or internal materials. `.gitignore` is not a security boundary and does not protect tracked files, force-added files, history, or artifacts. Before publication run `scripts/check-public-readiness.ps1 -RequireDocs` and independently inspect staged files, history, binaries, and embedded image data. Revoke and rotate exposed secrets as appropriate. Clean up sample resources and remaining snapshots and volumes without deleting existing Foundry or other users' resources.

Do not post secrets or exploit details in public issues. Report vulnerabilities through the repository's private security advisory feature if enabled, or otherwise contact maintainers through a private channel. Provide reproduction and impact without credentials.

[한국어](./architecture.md)

# Architecture and trust boundaries

![Trusted host and isolated execution paths](assets/overall-architecture.svg)

[Editable Draw.io source](assets/overall-architecture.drawio) · [README](../README-en.md) · [Deployment, execution and cleanup How-To](how-to-deploy-and-run-en.md)

The user and local CLI orchestrator and tool policy form the trusted area. The Foundry OpenAI v1 deployed model proposes code but does not choose endpoints, credentials, user ownership, or sessions. The policy limits allowed tools, arguments, turns, time, and output. Model output and tool results are untrusted and are not executed in a host subprocess. The host obtains the appropriate Entra tokens for each service without injecting them into the sandbox.

## Alternatives A / B

A routes an `identifier` to a PythonLTS session in `Microsoft.App/sessionPools`. The interpreter and files are reused for the same active ID, but are not guaranteed after idle expiry. Shell is a separate optional pool. B creates an isolated micro VM in `Microsoft.App/sandboxGroups`, and uses the SDK for exec, stop, resume, and deletion. Inside the sandbox it installs and controls the agent CLI toolchain (`git`, `jq`, `sqlite3`, and custom CLIs including `agent-tool` and `repo-audit`) and the loopback repository-index tool server (`state_worker.py`). Memory preserves RAM and root disk; Disk preserves the installed tools and root disk but restarts the server process. Creating a new clone from a named snapshot differs from resuming the existing instance. [A guide](dynamic-sessions-en.md) · [B guide](sandbox-en.md) · [State oracle](snapshot-comparison-en.md)

## Operational boundary

This is not an authenticated multi-user service. Before exposing it as a service, separately implement user authentication and session ownership, egress, resource/budget limits, audit and data retention policies. Do not treat the session ID alone as authorization. Memory snapshots can include sensitive RAM; external system state and connection consistency are not guaranteed. Do not delete existing Foundry or other users' resources during cleanup.

The diagrams were exported with Draw.io 32.6.1 and embed graphics from its Azure icon library. Where there is no dedicated Container Apps icon, the parent Container Apps Environment icon and an explicit label are used. [Azure official icon source and terms](https://learn.microsoft.com/en-us/azure/architecture/icons/).

[한국어](./architecture.md)

# Architecture and trust boundaries

![Trusted host and isolated execution paths](assets/overall-architecture.svg)

[Editable Draw.io source](assets/overall-architecture.drawio) · [README](../README-en.md) · [Deployment, execution and cleanup How-To](how-to-deploy-and-run-en.md)

The user and local CLI orchestrator and tool policy form the trusted area. The Foundry OpenAI v1 deployed model proposes code but does not choose endpoints, credentials, user ownership, or sessions. The policy limits allowed tools, arguments, turns, time, and output. Model output and tool results are untrusted and are not executed in a host subprocess. The host obtains the appropriate Entra tokens for each service without injecting them into the sandbox.

## Why separate the brain from execution?

In this sample the brain comprises the policy orchestrator on the trusted host and the remote Foundry model. The host manages inference and Azure tokens and validates model decisions. Running the orchestrator inside the sandbox could require injecting inference tokens or Azure credentials into the guest, increasing the risk of theft through untrusted code execution or prompt injection. Therefore credentials are not injected into the guest; only the CLIs and loopback in-memory server needed for tool execution reside in the isolated micro VM. This does not mean the model itself runs on the host.

Instead of registering each installed tool as a model function, the host supplies a tool catalog in the prompt and a single `run_sandbox_command` gateway. Model-proposed shell commands pass through host policy validation to the host-owned sandbox, and results are returned to the model. The policy's string checks do not provide complete command isolation, so credential separation, egress and resource restrictions, and other defenses remain necessary. [Detailed three-stage flow](sandbox-en.md#exposing-and-invoking-tools-for-an-external-agent-three-stages)

### Decoupling agent and sandbox update lifecycles

A design that bakes prompts, orchestration logic, model version, and business rules into an image or snapshot tightly couples them: changes require updating and redeploying that image or snapshot. This sample separates agent configuration from general guest tools (`git`, `jq`, `sqlite3`, repository auditing and indexing). As long as tool interfaces and compatibility remain stable, replacing the model (for example `gpt-5.4-mini` → `gpt-5.4`), changing prompts or host logic, or adding roles such as code review, security audit, or patching can happen without recreating the sandbox image or snapshot. Separately verify tool-calling support and access for the target model.

A reusable golden snapshot containing installed CLIs and the server program can be the basis for new sandbox clones used by different agents, while an existing sandbox can be stopped and resumed. `clone_provision_count=0` in the public clone example is evidence that **the new clone did not reinstall tools**, not that multiple agent connections or authorization isolation are implemented. Connecting multiple agents requires separate routing, ownership, and security controls; tool, guest configuration, or interface changes may require updating the snapshot.

## Alternatives A / B

A routes an `identifier` to a PythonLTS session in `Microsoft.App/sessionPools`. The interpreter and files are reused for the same active ID, but are not guaranteed after idle expiry. Shell is a separate optional pool. B creates an isolated micro VM in `Microsoft.App/sandboxGroups`, and uses the SDK for exec, stop, resume, and deletion. Inside the sandbox it installs and controls the agent CLI toolchain (`git`, `jq`, `sqlite3`, and custom CLIs including `agent-tool` and `repo-audit`) and the loopback repository-index tool server (`state_worker.py`). Memory preserves RAM and root disk; Disk preserves the installed tools and root disk but restarts the server process. Creating a new clone from a named snapshot differs from resuming the existing instance. [A guide](dynamic-sessions-en.md) · [B guide](sandbox-en.md) · [State oracle](snapshot-comparison-en.md)

## Operational boundary

This is not an authenticated multi-user service. Before exposing it as a service, separately implement user authentication and session ownership, egress, resource/budget limits, audit and data retention policies. Do not treat the session ID alone as authorization. Memory snapshots can include sensitive RAM; external system state and connection consistency are not guaranteed. Do not delete existing Foundry or other users' resources during cleanup.

The diagrams were exported with Draw.io 32.6.1 and embed graphics from its Azure icon library. Where there is no dedicated Container Apps icon, the parent Container Apps Environment icon and an explicit label are used. [Azure official icon source and terms](https://learn.microsoft.com/en-us/azure/architecture/icons/).

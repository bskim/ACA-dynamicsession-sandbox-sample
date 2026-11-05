[한국어](./troubleshooting.md)

# Troubleshooting

[README](../README-en.md) · [Dynamic Sessions](dynamic-sessions-en.md) · [Sandbox](sandbox-en.md)

| Symptom | What to check |
| --- | --- |
| Configuration error or placeholder | Clear unused `<...>` values in `.env` too, and check `/openai/v1/` in the inference URL. Process environment variables override `.env`. |
| 403 / authentication | Verify tenant/subscription, Foundry inference role, pool Session Executor or group SandboxGroup Data Owner, and role propagation. Do not automatically grant Contributor. |
| State missing under the same ID | Idle TTL or failure can allocate a new session. `--wait-for-ttl` must be long enough and `state_lost` is an application observation. Do not assume permanent recovery. |
| Shell or execution API error | Check the correct pool type, returned endpoint, API/wire profile, and preview versions. Set shell variables and cwd on each execution. |
| Deployment error | `ENABLE_PROVISIONING` is an environment variable for the setup process. Sandbox requires an existing group and `-Execute`. Check region, quota and RBAC. |
| Comparison or clone fails | Disk loses the old worker and requires bootstrap. Do not attach Data Disk to Memory. Check `python3` in the image, SDK, policy/polling, and the cleanup-failure manifest. |
| Connection lost after resume | Revalidate and reconnect external connections, leases, and tokens. Preserved memory does not guarantee a live external connection. |

Offline output does not prove Azure performance or real service behavior. Remove endpoints, IDs, command output, tokens and raw results before sharing diagnostics. [Sandbox Python SDK](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-quickstart-python-sdk) · [Session pool](https://learn.microsoft.com/en-us/azure/container-apps/session-pool).

## Cases observed in the live preview run (2026-11-05)

See also the [deployment, execution and cleanup How-To](how-to-deploy-and-run-en.md). These are observations for the tested versions, not universal explanations for every error.

| Symptom | Check and resolution |
| --- | --- |
| Sandbox deployment `ValidationForResourceFailed` | The `Microsoft.App/sandboxGroups@2026-07-01` Bicep resource required `properties: {}`. Check template properties even if internal details are empty. A local `BCP081` warning alone does not prove deployment failure. |
| `SessionPoolInvalidMaxConcurrentSessions` or 429 | The observed supported range is **5–600**. A value of 3 failed on PUT, and overlapping independent sessions at a 5-session ceiling caused 429. Check session count, pool settings and quota; increase the limit if needed. A 429 is not evidence of isolation failure. |
| Nested file GET 401 | Instead of encoding `agent-demo/source.py` as one route, put basename `source.py` in the route and `path=agent-demo` in the query. Do not automatically widen token permissions or RBAC. |
| Shell `SessionExecutionPropertyNotSupported` | The actual request body accepts only `{shellCommand, timeoutInSeconds}`. Do not send Python's `codeInputType`/`executionType`. Response `status="0"` is a string exit code and only 0 succeeds. |
| Windows `az.cmd` URL `&` | Even a quoted URL can have `&` interpreted as a command separator. Split parameters with `--url-parameters api-version=2025-10-02-preview "identifier=$existingSessionIdentifier"`. GET succeeded with correct CLI syntax. |
| Ubuntu cwd error or snapshot timeout | The default image lacked `/workspace`; initialize it with `mkdir -p /workspace` from `/`. A synchronous snapshot GET may omit `status`; verify existence of the matching ID. Creating/failure is not completion. Retry observation GET only on transient 404. |

401, 403 and 404 have different causes. Propagation polling with GET uses an existing active session; never automatically retry an execution or creation POST with an unknown outcome.
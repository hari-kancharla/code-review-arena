# Preflight

Run setup checks before spending time on an evaluation:

```bash
arena preflight benchmark_sets/audit_v1 --reviewer reference-patch --allow-local-execution
arena preflight benchmark_sets/audit_v1 --mode review --reviewer http:http://localhost:8080/review --json
```

`python -m arena` is equivalent to the installed `arena` command. The older
`python -m arena.preflight` remains a two-field environment diagnostic.

## Checks and exit status

- Python minimum version and immutable-snapshot pack validation.
- Reviewer factory acceptance, HTTP(S) URL syntax, command quoting, and host
  command executable availability. Commands and endpoint credentials are not echoed.
- For `patch`/`full` modes: declared test images, explicit local-execution opt-in,
  configured trusted-pack hash admission, and pytest availability in the harness
  interpreter when bare Python/pytest commands use it.
- Required Docker images use the executor's bounded daemon/image probes. Unique
  images are checked once; a missing daemon skips image probes. No implicit pull
  or local fallback is permitted. Review-only mode does not check test images,
  but a selected `docker-command` reviewer still requires its own image.

Exit **1** means a check failed, in both table and `--json` modes. Exit **0**
means no implemented check failed, not that the evaluation is certified. Warnings
identify untested assumptions. JSON has `schema_version`, `ok`, `scope`, and
`checks` (`name`, `status`, `detail`). With no reviewer, reviewer setup is explicitly
unchecked. Full mode is preflight's default so it checks test prerequisites;
`arena run` defaults to review mode. Pass the intended `--mode` to both commands.

## Limits

Preflight never invokes a reviewer, model endpoint, fixture test, or container.
It leaves the source pack unchanged but creates and removes a temporary snapshot.
Docker CLI queries contact the configured Docker daemon (which may be remote).

It does **not** verify API credentials, model availability, image contents or
in-image executables, wrapper payload consumption, output contracts, other test
runners, arbitrary case dependencies, resource capacity, or benchmark outcomes.
Use `arena verify-reviewer` to actually run a trusted host wrapper against a case,
then run the appropriate pack certification/evaluation. A built-in reviewer
being accepted by the factory is not proof that its behavior works on your pack.

## Comparison with established harnesses

The [SWE-bench Docker setup guide](https://www.swebench.com/SWE-bench/guides/docker_setup/)
uses version checks and a live `hello-world` container test and documents disk/RAM
requirements. [Inspect sandboxing](https://inspect.aisi.org.uk/sandboxing.html)
documents container resource controls, cleanup, and tracing for failures.
Reviewed September 17, 2026. Arena's static preflight complements, not replaces,
live execution verification. These sources do not establish that Arena's isolation
or benchmark quality exceeds either system.

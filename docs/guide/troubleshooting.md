<!-- doc_type: how-to -->
<!-- doc_tier: everyday -->

# Fix an error

Find the exception you caught, learn what it means and take the next step.
Every exception derives from the ocx exit code, so the code and the class
always agree. This page follows the "know what went wrong" need in the
[docs use-case note](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.agents/research/docs-use-cases.md).

## Read the exception

Catch `OcxProcessError` for a failed command and read `.exit_code`. The
message carries a next step and the redacted stderr. Catch
`OcxExecutionError` to cover a non-zero exit and a timeout together:

```python-contract
import tempfile
from pathlib import Path

from ocx_sdk import ConfigError, Ocx

root = Path(tempfile.mkdtemp())
(root / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')
project = Ocx().project(root)

try:
    project.lock(check_only=True)
except ConfigError as error:
    print(error.exit_code)
```

No `ocx.lock` exists yet, so the check raises `ConfigError` (exit 78).

## Common failures

| Symptom | Exception (exit) | Next step |
|---|---|---|
| `OcxNotFoundError` on `Ocx()` | none | Pass `exe=`, set `OCX_SDK_EXE`, put `ocx` on `PATH`, or call [`bootstrap.ensure()`](bootstrap.md). |
| `lock(check_only=True)` fails with a missing lock | `ConfigError` (78) | Run `project.lock()` and commit `ocx.lock`. |
| `lock(check_only=True)` fails on drift | `DataError` (65) | `ocx.toml` changed. Run `project.lock()` and commit the result. |
| `AttributeError` on `ocx.exec` or `ocx.run` | none | Use `ocx.project(path).exec(argv)`, or `ocx.invoke(argv)` for raw argv. |
| A tool or package is not in the registry | `NotFoundError` (79) | Check the identifier and tag. |
| A registry rejects the credentials | `AuthError` (80) | Supply credentials with `OcxConfig.auth` or `OCX_AUTH_*`. The SDK never retries this. |
| A registry call fails transiently | `TempFailError` (75) | The SDK retries it by default. Tune `RetryPolicy` if the default is too short. |
| A step runs too long | `OcxTimeoutError` | Raise `timeout=` or fix the step. |
| The tool you ran failed | `OcxProcessError` | Read `.exit_code` and `.stderr`. Pass `check=False` to inspect it. |

The [exit-code table](../reference/environment.md#exit-codes) lists every
code. [Errors and credentials](concepts/errors-and-security.md) explains the
model and the error envelope.

## Check the binary version

The SDK refuses an `ocx` below its supported floor. Run
`Ocx().version()` to see which binary you have, and read
[Compatibility](concepts/compatibility.md) for the supported range.

## See also

- [Reproduce a toolchain in CI](reproducible-ci.md): drift checks.
- [Concurrency and timeouts](concepts/concurrency.md): retries and timeouts.

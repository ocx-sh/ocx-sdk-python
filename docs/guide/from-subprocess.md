<!-- doc_type: how-to -->
<!-- doc_tier: everyday -->

# Replace a hand-rolled subprocess wrapper

Swap a `subprocess.run(["ocx", ...])` wrapper for typed methods, so you stop
parsing JSON and exit codes yourself. This page follows the migration need in
the [docs use-case note](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.agents/research/docs-use-cases.md).

You need an `ocx` binary and a project. The first fence creates a throwaway
one.

## Start from the wrapper

A typical wrapper shells out, checks the code and decodes the JSON:

```python-no-run
# illustrative: the code this page replaces; "ocx.toml" stands in for your project file.
import json
import subprocess

done = subprocess.run(
    ["ocx", "--project", "ocx.toml", "--format", "json", "status"],
    capture_output=True,
    text=True,
)
if done.returncode != 0:
    raise RuntimeError(done.stderr)
status = json.loads(done.stdout)
```

## Call the typed method

```python-contract
import tempfile
from pathlib import Path

from ocx_sdk import Ocx

root = Path(tempfile.mkdtemp())
(root / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')
project = Ocx().project(root)
project.lock()

status = project.status()
print(status.lock.present, status.lock.current)
```

[`status()`](../reference/api.md#ocx_sdk.Project.status) adds the `--project`
and `--format json` flags, parses the output into a frozen
[`StatusReport`](../reference/api.md#ocx_sdk.StatusReport) and raises a typed
exception on a failure. The call exits `0` even when the lock is stale, so
read `status.lock.current` rather than catching an error.

## Map the pieces

| Hand-rolled code | SDK |
|---|---|
| `returncode == 78` | `except ConfigError` |
| `returncode == 65` | `except DataError` |
| `json.loads(stdout)` | a result struct such as `StatusReport` |
| `env={**os.environ, ...}` | `project.env().compose().mapping` |
| `["ocx", "exec", "--", ...]` | `project.exec([...])` |
| a command the SDK does not type | `ocx.invoke([...])` |

The [command map](../reference/command-map.md) lists every typed command.
Keep `invoke()` for the rest. It still composes the environment, but it
parses nothing.

## See also

- [Fix an error](troubleshooting.md): the exception for each exit code.
- [Why a wrapper](concepts/why-a-wrapper.md): what the SDK leaves to ocx.

<!-- doc_type: how-to -->
<!-- doc_tier: everyday -->

# Run pinned tools from a script

Run a project's pinned tools from a Python script or test, at the exact
versions in its `ocx.lock`, with nothing installed on the host. This page
follows the use case "run my build or test tool at a pinned version" from the
[docs use-case note](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.agents/research/docs-use-cases.md).

You need an `ocx` binary, found through `PATH`, `OCX_SDK_EXE` or
[`bootstrap.ensure()`](bootstrap.md), and a project directory that holds an
`ocx.toml`. The steps below create a throwaway project so you can run them as
written. Point `project_dir` at your own project instead.

## Open the project

```python-contract
import tempfile
from pathlib import Path

from ocx_sdk import Ocx

project_dir = Path(tempfile.mkdtemp())
(project_dir / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')

project = Ocx().project(project_dir)
project.lock()
```

[`Ocx.project()`](../reference/api.md#ocx_sdk.Ocx.project) takes the
directory or the `ocx.toml` file. Every call on the handle carries the
project path, so nothing depends on the working directory. `lock()` is only
needed once, when the project has no `ocx.lock` yet.

## Pull the toolchain

```python-contract
project.pull()
```

[`pull()`](../reference/api.md#ocx_sdk.Project.pull) fills the object store
from `ocx.lock`, so a later `exec` finds everything cached.

## Run a tool

```python-contract
result = project.exec(["task", "--version"])
assert result.exit_code == 0
print(result.stdout)
```

[`exec()`](../reference/api.md#ocx_sdk.Project.exec) waits for the child and
captures its output. A non-zero exit raises
[`OcxProcessError`](../reference/api.md#ocx_sdk.OcxProcessError). To inspect a
failing step instead, pass `check=False`:

```python-contract
failed = project.exec(["sh", "-c", "exit 3"], check=False)
assert failed.exit_code == 3
```

Use `capture=False` to stream a long build to the terminal, and `timeout=` to
bound it. The SDK never retries a child process.

## Pass the environment to your own subprocess

When you want to run the child yourself, take the composed environment as a
plain `dict`:

```python-contract
import subprocess

environment = project.env().compose().mapping
done = subprocess.run(["task", "--version"], env=environment, capture_output=True, text=True, check=True)
print(done.stdout)
```

[`ComposedEnv.mapping`](../reference/api.md#ocx_sdk.ComposedEnv) is safe to
use from several threads. Its `activate()` context manager changes
`os.environ` for the whole process, so prefer `mapping` in concurrent code.

## See also

- [Projects and toolchains](projects.md): the full `Project` surface.
- [Use the async API](async.md): the same calls under asyncio.
- [Fix an error](troubleshooting.md): what each exception means.

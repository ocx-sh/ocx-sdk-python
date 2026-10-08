<!-- doc_type: tutorial -->
<!-- doc_tier: first-steps -->

# Run your first pinned tool

In this tutorial you install the SDK, get an `ocx` binary, pin one tool in a
throwaway project and run it from Python. It takes about five minutes. You
need Python 3.12+ and network access.

## Install the SDK

```bash
uv add ocx-sdk
```

The wheel has no runtime dependencies.

## Get an ocx binary

`bootstrap.ensure()` downloads a verified `ocx` binary into its own cache and
returns the path. A cache hit needs no network. Hand that path to `Ocx`:

```python-contract
from ocx_sdk import Ocx, bootstrap

ocx = Ocx(exe=bootstrap.ensure())
print(ocx.version())
```

The last line prints the version of the binary, for example `0.6.5`. That
proves the SDK can talk to ocx.

## Pin a tool in a project

A project is a directory with an `ocx.toml` that lists tools. Create one in a
temporary directory with a single tool, the task runner:

```python-contract
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp())
(root / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')

project = ocx.project(root)
project.lock()
```

`lock()` resolves the tag `task:3` to a digest for every platform and writes
`ocx.lock` beside `ocx.toml`. The project runs exactly that build from here on.

## Pull and run the tool

```python-contract
project.pull()
result = project.exec(["task", "--version"])
print(result.exit_code, result.stdout)
```

`pull()` downloads what `ocx.lock` pins. `exec()` runs the command inside the
project's environment, so `task` resolves to the pinned build and not to
anything on your `PATH`. The output is an exit code of `0` and the task
version, for example `3.54.0`.

You have a pinned toolchain driven from Python.

## Next steps

- [Run pinned tools from a script](run-pinned-tools.md): the same flow for a project you already have.
- [Test against a pinned tool in pytest](pytest-fixture.md): one locked project per test.
- [Reproduce a toolchain in CI](reproducible-ci.md): pin ocx itself and fail on drift.
- [Why a wrapper](concepts/why-a-wrapper.md): what the SDK leaves to the binary.

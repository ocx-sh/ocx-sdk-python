<!-- doc_type: how-to -->
<!-- doc_tier: everyday -->

# Resolve and pull for another platform

Resolve a project's lock, preview a pull or compose an environment for a
platform other than the host, for example `linux/arm64` from a macOS laptop.
This page follows the cross-platform need in the
[docs use-case note](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.agents/research/docs-use-cases.md).

You need an `ocx` binary and a project with an `ocx.toml`. The first step
creates a throwaway one.

## Lock for the target

`ocx.lock` already records one digest per platform. Resolve for a specific
one by passing `platform=` as an `os/arch` string:

```python-contract
import tempfile
from pathlib import Path

from ocx_sdk import Ocx

root = Path(tempfile.mkdtemp())
(root / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')
project = Ocx().project(root)
project.lock(platform="linux/arm64")
print(sorted(project.status().groups["default"].tools["task"].platforms))
```

The last line lists every platform the lock covers, such as `linux/arm64` and
`windows/amd64`.

## Preview a pull

`pull(dry_run=True)` reports what a pull would fetch and writes nothing. It
returns one `DryRunEntry` per package:

```python-contract
for entry in project.pull(dry_run=True, platform="linux/arm64"):
    print(entry.package, entry.status)
```

A `status` of `would-fetch` means the package is not cached yet.

## Use the same argument elsewhere

`update`, `env`, `inspect` and the package-tier calls accept `platform=` too.
The [command map](../reference/command-map.md) lists them. A binary built for
another platform cannot run on the host, so use `exec` only with the host
platform.

For `ocx` itself, [`bootstrap.ensure()`](bootstrap.md) picks the archive for
the host OS and architecture, so it needs no argument.

## See also

- [Projects and toolchains](projects.md): the lifecycle methods.
- [Reproduce a toolchain in CI](reproducible-ci.md): lock checks.

<!-- doc_type: how-to -->
<!-- doc_tier: integration -->

# Reproduce a toolchain in CI

Make a CI job install a pinned `ocx`, pull the toolchain that `ocx.lock` pins,
and fail when the lock no longer matches `ocx.toml`. This page follows the
reproducible-CI use case in the
[docs use-case note](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.agents/research/docs-use-cases.md).

You need a project with a committed `ocx.toml` and `ocx.lock`, and a runner
with network access.

## Pin the binary in the workflow

If a job only needs the tools on `PATH`, skip Python. The SDK's own
[CI workflow](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.github/workflows/ci.yml)
uses the `setup-ocx` action, which installs the pinned binary and activates
the project:

```yaml
- uses: ocx-sh/setup-ocx@25fa771f8572572dc64528db89560de68a163a0e  # v1
  with:
    version: "0.6.2"
- name: Verify
  run: task verify
```

Pin the action by full commit SHA, as above.

## Pin the binary from Python

When a Python step drives the toolchain, let the SDK provision the binary.
[`bootstrap.ensure()`](../reference/api.md#ocx_sdk.ensure) downloads the
release you name, verifies it and caches it:

```python-contract
import tempfile
from pathlib import Path

from ocx_sdk import Ocx, bootstrap

ocx = Ocx(exe=bootstrap.ensure(version="0.6.2"))

root = Path(tempfile.mkdtemp())
(root / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')
project = ocx.project(root)
project.lock()
```

Name an exact `version=`. The default follows the stable channel, so
two runs a week apart can differ.

## Fail the build on drift

`lock(check_only=True)` verifies the lock and writes nothing. It returns
`None` when the lock is current:

```python-contract
assert project.lock(check_only=True) is None
```

When `ocx.toml` has changed since the last `ocx lock`, it raises
[`DataError`](../reference/api.md#ocx_sdk.DataError). A missing lock raises
[`ConfigError`](../reference/api.md#ocx_sdk.ConfigError). Both are
non-retryable, so let them fail the job:

```python-contract
from ocx_sdk import DataError

(root / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\nuv = "ocx.sh/astral-sh/uv:0"\n')
try:
    project.lock(check_only=True)
except DataError as error:
    print(error.exit_code)
else:
    raise AssertionError("expected drift")
```

Then pull and run:

```python-contract
(root / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')
project.pull()
project.exec(["task", "--version"])
```

## Harden the job

A CI runner's ambient environment chooses which binary runs and which
credentials attach to registry calls. Pass `exe=`, a minimal `HostEnv` and an
empty `insecure_registries` to take those choices away from it. See
[Hermetic CI](hermetic-ci.md) for every lever, and
[Vendoring a `dist.json`](vendoring.md) for air-gapped mirrors.

## See also

- [Bootstrap](bootstrap.md): pinning, channels and mirrors.
- [Fix an error](troubleshooting.md): what `DataError` and `ConfigError` mean.

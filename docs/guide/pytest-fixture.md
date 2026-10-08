<!-- doc_type: how-to -->
<!-- doc_tier: everyday -->

# Test against a pinned tool in pytest

Give each test a locked project and run a pinned tool inside it, without
installing the tool on the test machine. This page follows the "test against a
pinned tool in pytest" need from the
[docs use-case note](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.agents/research/docs-use-cases.md).

You need `pytest`, the SDK, and an `ocx` binary found through `PATH`,
`OCX_SDK_EXE` or [`bootstrap.ensure()`](bootstrap.md).

## Write a project factory

Put the setup in one plain function. It writes an `ocx.toml`, locks it and
returns the `Project` handle:

```python-contract
import tempfile
from pathlib import Path

from ocx_sdk import Ocx, Project


def make_project(root: Path, body: str) -> Project:
    (root / "ocx.toml").write_text(body)
    project = Ocx().project(root)
    project.lock()
    return project


project = make_project(Path(tempfile.mkdtemp()), '[tools]\ntask = "ocx.sh/go-task/task:3"\n')
assert project.exec(["task", "--version"]).exit_code == 0
```

The SDK's own contract suite builds its projects the same way in
`tests/contract/conftest.py`.

## Wrap it in a fixture

```python-no-run
# illustrative: a fixture only runs under pytest, and its body is the factory above.
import pytest

from ocx_sdk import Ocx, Project


@pytest.fixture(scope="session")
def ocx() -> Ocx:
    return Ocx()


@pytest.fixture
def project(ocx: Ocx, tmp_path) -> Project:
    (tmp_path / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')
    handle = ocx.project(tmp_path)
    handle.lock()
    return handle


def test_task_runs(project: Project) -> None:
    assert project.exec(["task", "--version"]).exit_code == 0
```

Build the `Ocx` handle once per session. It is frozen and safe to share. Use
`tmp_path` for the project so tests cannot see each other's lock files.

## Keep the test hermetic

Point the handle at a throwaway store and drop the ambient environment, so a
developer's shell cannot change the result:

```python-contract
from ocx_sdk import HostEnv, OcxConfig

hermetic = Ocx(
    config=OcxConfig(home=Path(tempfile.mkdtemp())),
    host_env=HostEnv.minimal(),
)
print(hermetic.version())
```

[`HostEnv.minimal()`](../reference/api.md#ocx_sdk.HostEnv) keeps only `PATH`,
`HOME` and `TMPDIR`. See [Hermetic CI](hermetic-ci.md) for the other levers.

## See also

- [Run pinned tools from a script](run-pinned-tools.md): the calls the factory uses.
- [Concurrency and timeouts](concepts/concurrency.md): sharing one handle across threads.

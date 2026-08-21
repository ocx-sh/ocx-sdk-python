# Quickstart

## Install

```bash
uv add ocx-sdk
```

or, with `pip`:

```bash
pip install ocx-sdk
```

Requires Python 3.12+. Zero runtime dependencies — the wheel pulls in
nothing but itself.

## The canonical CI journey

Bootstrap a pinned ocx binary, resolve a project, pull its declared
toolchain, run inside it. This is the shape almost every consumer of this
SDK ends up writing:

```python-no-run
# illustrative: /srv/build stands in for a real project directory (one
# holding ocx.toml), and bootstrap.ensure() needs network access — swap in
# your own path to run this for real.
from ocx_sdk import Ocx, bootstrap

ocx = Ocx(exe=bootstrap.ensure())
project = ocx.project("/srv/build")
project.pull()
result = project.run(["task", "verify"])
print(result.exit_code, result.stdout)
```

- [`bootstrap.ensure()`](../reference/api.md#ocx_sdk.ensure)
  downloads, verifies, and caches a pinned ocx binary, then returns its path.
  It is idempotent — a cache hit that still hashes correctly needs no network
  at all. See [Bootstrap](bootstrap.md) for pinning and mirrors.
- [`Ocx(exe=...)`](../reference/api.md#ocx_sdk.Ocx) resolves the binary once
  and pins it to the handle for its lifetime; the handle is frozen and
  thread-safe.
- [`ocx.project(path)`](../reference/api.md#ocx_sdk.Ocx.project) returns a
  [`Project`](../reference/api.md#ocx_sdk.Project) whose every call carries
  `--project <path>` explicitly, so nothing depends on the working
  directory. See [Projects & toolchains](projects.md).
- [`project.pull()`](../reference/api.md#ocx_sdk.Project.pull) materializes
  everything `ocx.lock` declares — the "install" step of the journey.
- [`project.run([...])`](../reference/api.md#ocx_sdk.Project.run) runs a
  command inside the project's composed environment and hands back its exit
  code and output.

If a pinned binary is already on `PATH` or named by `OCX_SDK_EXE` — the
shape a CI image typically arranges — the simplest possible live check
needs no project at all:

```python-contract
from ocx_sdk import Ocx

ocx = Ocx()
print(ocx.version())
```

## Two things to unlearn from the CLI

!!! note "There is no `ocx.run`"
    Raw argv — anything the SDK doesn't type — goes through
    [`ocx.invoke(argv)`](../reference/api.md#ocx_sdk.Ocx.invoke) (or
    `invoke_async`/`spawn`/`spawn_async`). The toolchain runner that mirrors
    `ocx run` on the CLI is project-tier: `ocx.project(path).run(argv)`.
    Reaching for `ocx.run` or `ocx.exec` raises `AttributeError` with a
    pointer to the right method — the SDK reserves those two names on
    purpose.

!!! note "Package-tier commands are machine tier"
    [`ocx.package`](../reference/api.md#ocx_sdk.Ocx.package) — install,
    select, exec, and the author flow (`create`/`test`/`push`) — operates on
    `$OCX_HOME` directly and takes no project path. It is a sibling of
    `ocx.project(...)`, not something reached through it.

## Where to go next

- [Bootstrap](bootstrap.md) — pinning, corporate mirrors, `HostEnv` tiers.
- [Projects & toolchains](projects.md) — `Project` in full: env composition,
  `run` vs `spawn`.
- [Hermetic CI](hermetic-ci.md) — the threat-model levers for a build that
  doesn't trust its ambient environment.
- [Authoring packages](authoring.md) — `create` → `test` → `push`.
- [Command ↔ method map](../reference/command-map.md) — every ocx command and
  its SDK method, if any.

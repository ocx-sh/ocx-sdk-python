<!-- doc_type: how-to -->
<!-- doc_tier: everyday -->

# Projects & toolchains

[`Project`](../reference/api.md#ocx_sdk.Project) is the project-tier handle,
obtained from [`Ocx.project(path)`](../reference/api.md#ocx_sdk.Ocx.project) —
never constructed directly. Every call carries `--project <path>`
explicitly, so no `Project` method depends on the process's working
directory, and an ambient `OCX_PROJECT` can never retarget one (the SDK
neutralizes it on every spawn).

```python-no-run
# illustrative: /srv/build stands in for a real project directory.
from ocx_sdk import Ocx

project = Ocx().project("/srv/build")
project.add("ocx.sh/go-task/task:3", group="ci")
project.lock()
report = project.env()
environment = report.compose().mapping
```

## The toolchain lifecycle

The lifecycle methods are `init`, `add`, `remove`, `lock`, `update`,
`pull`, `status`, `inspect` and `env`. The
[command map](../reference/command-map.md) lists each with its ocx command.
`status` exits `0` even when the lock is broken, so read the report. `env`
returns the composed environment, described below.

`lock`, `update`, and `pull` all accept `platform=` to resolve against a
target other than the host, and `pull`/`add`/`lock`/`update` share a
`pull: bool | None` kwarg that leaves the materialize-or-not choice to ocx's
own default when omitted.

## Env composition: `EnvReport` → `ComposedEnv`

[`Project.env()`](../reference/api.md#ocx_sdk.Project.env) returns an
[`EnvReport`](../reference/api.md#ocx_sdk.EnvReport) whose `entries` are the
toolchain's `[env]` contributions, in declaration order.
[`EnvReport.compose()`](../reference/api.md#ocx_sdk.EnvReport.compose) folds
them into a [`ComposedEnv`](../reference/api.md#ocx_sdk.ComposedEnv):

```python
import os

from ocx_sdk import EnvReport

# ocx always writes all five arrays, so a hand-built payload carries them too.
report = EnvReport.from_json(
    '{"entries": ['
    '{"key": "JAVA_HOME", "type": "constant", "value": "/opt/jdk"},'
    '{"key": "PATH", "type": "path", "value": "/opt/jdk/bin"}'
    '], "binaries": [], "entrypoints": [], "integrations": [], "advisories": []}'
)
composed = report.compose(base={"PATH": "/usr/bin"})
assert composed.mapping["JAVA_HOME"] == "/opt/jdk"
# PathVar prepends, joined with the platform's own separator:
assert composed.mapping["PATH"].split(os.pathsep) == ["/opt/jdk/bin", "/usr/bin"]
```

A report produced through a real `Project.env()` call carries the handle's
own host snapshot as `compose()`'s default base, so composing a report from
a hermetic handle (`HostEnv.clean()`) stays hermetic — you only pass `base=`
explicitly to override it, exactly as the example above does.

`ComposedEnv` has two ways to use the result:

- **`.mapping`** — a plain `dict`, the non-invasive form. Pass it straight to
  `subprocess.run(..., env=...)` or any other API that takes an environment
  mapping. Safe under concurrency.
- **`.activate()`** — a context manager that applies the environment to
  `os.environ` for the duration of the block. **Process-global and
  single-owner**: it changes what every thread and subprocess sees, and a
  second, overlapping `activate()` raises `RuntimeError` instead of nesting.
  The revert on exit is diff-based — only the keys this environment set are
  restored, absent keys are deleted again, and any unrelated mutation made
  inside the block survives. Concurrent code should use `.mapping` instead.

```python-no-run
# illustrative: needs a real EnvReport produced by Project.env().
with composed.activate():
    subprocess.run(["some-tool"])  # sees the composed environment
# os.environ is back to what it was before the block, for exactly the
# keys `composed` touched.
```

## `exec` vs `spawn`

Both compose `ocx exec --project ... [NAMES] -- ARGV` under the hood, and
both accept `names=`, `groups=`, `clean=` (strip the ambient parent
environment before composing), `env=` (extra `[env]` entries for this call
only), and `lazy_mode=`.

- [`exec`](../reference/api.md#ocx_sdk.Project.exec) /
  [`exec_async`](../reference/api.md#ocx_sdk.Project.exec_async) — a
  one-shot: waits, captures (unless `capture=False`), and by default raises
  `OcxProcessError` on a non-zero child exit. `check=False` is how you
  inspect a failing build instead of catching an exception. Child processes
  are **never retried**, regardless of session `RetryPolicy` — retrying a
  build step could re-run side effects the first attempt already caused.
- [`spawn`](../reference/api.md#ocx_sdk.Project.spawn) /
  [`spawn_async`](../reference/api.md#ocx_sdk.Project.spawn_async) — starts
  the command and hands back the live `Popen` /
  `asyncio.subprocess.Process`. No SDK timeout, no output pump: waiting,
  draining pipes, and killing belong to the caller, exactly like a bare
  `Popen`.

`capture=False` on `exec` inherits stdio and forwards `SIGINT` to the child —
the shape you want for a long-running build step whose output should stream
straight to the terminal.

## Scoping configuration

[`with_config(**overrides)`](../reference/api.md#ocx_sdk.Project.with_config)
derives a `Project` that shares this one's binary, host environment, and
`on_log`, with only the named `OcxConfig` fields replaced:

```python-no-run
# illustrative: needs a real Project handle.
ci_project = project.with_config(offline=True, timeout=30.0)
```

See [Errors & credentials](concepts/errors-and-security.md) for the
credential-scoping pattern this same method enables (`with_config(auth={})`
after a `pull()`).

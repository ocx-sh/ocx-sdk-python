<!-- doc_type: landing -->
<!-- doc_tier: first-steps -->

# ocx-sdk

Run pinned developer tools from Python through typed methods, sync or async, with no runtime dependencies.

## Install

`ocx-sdk` drives the [ocx](https://ocx.sh) binary, so you need Python 3.12+ and
an `ocx` binary, either on `PATH` or fetched by `bootstrap.ensure()`.

```bash
uv add ocx-sdk
```

:::warning[Pre-1.0: the API may change between minor versions]
Breaking changes ship without migration shims. Pin to a version, and watch the
[release notes](https://github.com/ocx-sh/ocx-sdk-python/releases).
:::

## Start with a task

- [Run your first pinned tool](guide/quickstart.md): install, get an ocx binary, pull a toolchain and run a tool from it.
- [Run pinned tools from a script](guide/run-pinned-tools.md): pull a project's toolchain and run its tools, or hand its environment to your own subprocess.
- [Test against a pinned tool in pytest](guide/pytest-fixture.md): one fixture, one locked project per test.
- [Reproduce a toolchain in CI](guide/reproducible-ci.md): bootstrap a pinned ocx, then fail the build when the lock drifts.
- [Resolve for another platform](guide/cross-platform.md): lock and pull for a target other than the host.
- [Use the async API](guide/async.md): run tools from asyncio without blocking.
- [Replace a hand-rolled subprocess wrapper](guide/from-subprocess.md): map `subprocess.run(["ocx", ...])` onto typed methods.
- [Fix an error](guide/troubleshooting.md): find the exception and the next step by exit code.

## Look something up

- [API reference](reference/api.md): every public symbol, generated from docstrings.
- [Command map](reference/command-map.md): every ocx command and its SDK method.
- [Environment and exit codes](reference/environment.md): every variable the SDK reads or writes.
- [Why a wrapper](guide/concepts/why-a-wrapper.md): why the SDK drives the CLI instead of reimplementing it.

## See also

- [Source on GitHub](https://github.com/ocx-sh/ocx-sdk-python)
- [ocx, the binary package manager](https://github.com/ocx-sh/ocx)
- [The OCX project portal](https://ocx.sh)

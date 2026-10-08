<!-- doc_type: how-to -->
<!-- doc_tier: everyday -->

# Use the async API

Run tools from an asyncio service without blocking the event loop, bound them
with a timeout and cancel them safely. This page follows the async need in the
[docs use-case note](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.agents/research/docs-use-cases.md).

You need an `ocx` binary and a locked project. The first step creates a
throwaway one.

## Know what is async

Three methods have async twins: `invoke_async`, `exec_async` and
`spawn_async`, on `Ocx`, `Project` and `package`. Typed calls such as `lock`,
`pull` and `status` are synchronous. Call them before you start the loop, or
from `asyncio.to_thread`.

## Run tools concurrently

```python-contract
import asyncio
import tempfile
from pathlib import Path

from ocx_sdk import Ocx

root = Path(tempfile.mkdtemp())
(root / "ocx.toml").write_text('[tools]\ntask = "ocx.sh/go-task/task:3"\n')
project = Ocx().project(root)
project.lock()


async def main() -> None:
    first, second = await asyncio.gather(
        project.exec_async(["task", "--version"]),
        project.exec_async(["task", "--help"]),
    )
    print(first.exit_code, second.exit_code)


asyncio.run(main())
```

Handles are frozen, so many tasks can share one `project`.

## Set a timeout

Pass `timeout=` in seconds. A child that outlives it is terminated, and the
call raises [`OcxTimeoutError`](../reference/api.md#ocx_sdk.OcxTimeoutError):

```python-contract
from ocx_sdk import OcxTimeoutError


async def bounded() -> None:
    try:
        await project.exec_async(["sh", "-c", "sleep 30"], timeout=1)
    except OcxTimeoutError as error:
        print(error.timeout)


asyncio.run(bounded())
```

The timeout counts per attempt, and the SDK never retries a child process.

## Cancel a call

Cancelling a task that awaits an `*_async` call terminates the child before
`CancelledError` propagates, so no orphan process stays behind. See
[Concurrency and timeouts](concepts/concurrency.md) for the kill ladder and
the Windows limits.

## Avoid `on_log`

`Ocx(on_log=...)` is rejected on async paths, because the callback would run
on the event loop. Read `result.stderr` after the call instead.

## See also

- [Run pinned tools from a script](run-pinned-tools.md): the synchronous calls.

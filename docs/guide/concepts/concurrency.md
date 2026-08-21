# Concurrency & timeouts

## Handles are frozen and cache-free

`Ocx`, `Project`, and the command namespaces (`package`, `config`, `patch`)
hold no mutable state. The only thing captured at construction is the
resolved binary path — discovery runs once, in `Ocx.__init__`, never again,
not even when [`with_config`](../../reference/api.md#ocx_sdk.Ocx.with_config)
derives a new handle from an existing one. Every call composes its own argv
and its own child environment from scratch (the host environment is
snapshotted fresh, `dict(source)`, at spawn time — no stored reference
survives between calls). That combination is what makes a handle safe to
share across threads, `asyncio` tasks, and event loops without a lock: there
is no shared mutable state to protect.

The one exception, and it is deliberately benign: the compatibility gate
(see [Compatibility](compatibility.md)) memoizes into a one-slot cell shared
by every handle derived from one construction. Two threads racing the first
typed call may both probe `ocx version` and both write the same answer — a
harmless double-check, not a correctness bug.

## `on_log` and concurrent pump threads

`Ocx(on_log=callback)` fires `callback` from a per-spawn stderr pump thread,
one thread per running sync call. Concurrent calls mean concurrent,
unserialized invocations of the same callback — it must be thread-safe.
Python's stdlib `logging` module qualifies out of the box, which is why
`callback=logger.info`-shaped usage is the common case. **`on_log` is
rejected outright on `*_async` paths** in v0.1: the async pump would have to
fire the callback on the event loop, where a blocking callback stalls
everything else running on it. A deliberate async streaming contract is
future work, not an accidental one shipped early.

## `ComposedEnv.activate()` is the one process-global exception

Everything above is per-call and lock-free *except*
[`ComposedEnv.activate()`](../../reference/api.md#ocx_sdk.ComposedEnv.activate),
which by its nature mutates `os.environ` — a genuinely process-global
resource. It is single-owner: a second, overlapping `activate()` call raises
`RuntimeError` rather than nesting, and there is no lock, because
serializing callers would fake a safety property (queued, ordered access)
the API doesn't actually provide. Concurrent code should read
`ComposedEnv.mapping` instead and pass it explicitly wherever an environment
mapping is accepted — see [Projects & toolchains](../projects.md#env-composition-envreport-composedenv).

## Retries {: #retries }

[`RetryPolicy`](../../reference/api.md#ocx_sdk.RetryPolicy) only ever
retries what ocx itself classified as transient — exit code `75` by
default (`retry_on`), the taxonomy described in full on the
[environment & exit codes](../../reference/environment.md#exit-codes) page.
A few things are true regardless of policy:

- **`timeout` is per attempt**, not for the whole retry sequence — the
  worst case is `attempts × timeout + Σ backoff`.
- **Auth failures, checksum mismatches, and `401`/`403`/`404` are never
  retried**, whatever `retry_on` says — retrying those wastes an attempt on
  a failure retrying cannot fix.
- **`Ocx.login`** and **`package.push`** default their per-call `retry` to
  `None` regardless of session configuration: a timed-out login exits `75`,
  and re-sending credentials on a timeout is exactly the mistake the
  never-retry-auth rule exists to prevent; a push is a registry write, and
  a retried timeout risks publishing twice. Pass `retry=` explicitly on
  either call to opt back in.
- **Child processes started through `run`, `exec`, or `spawn` are never
  retried**, ever — there is no session policy that reaches them. Retrying
  a build step could re-run side effects the first attempt already caused.
- A retry's delay is full jitter (drawn uniformly from `[0, delay]`), and a
  server's `Retry-After` header is honored up to its own ceiling
  (`max_retry_after`) rather than truncated to `max_backoff` — truncating it
  would hammer a registry that just asked for room.

## Timeouts & cancellation

Enforcement on POSIX: `communicate(timeout)`, then `terminate()`, a grace
period, then `kill()` — sent to the child's whole process group. Expiry
raises [`OcxTimeoutError`](../../reference/api.md#ocx_sdk.OcxTimeoutError)
carrying the argv and whatever stderr was captured before the kill. Timeouts
are not retried by default, for the same reason auth failures aren't: a
timeout says nothing about whether retrying would help.

Cancelling an `asyncio` task awaiting an `*_async` call terminates the
child before `CancelledError` propagates — CPython's own `asyncio` does not
do this for you ([gh-88050](https://github.com/python/cpython/issues/88050)),
so the SDK does it explicitly.

Every `*_async` method spawns through `asyncio.create_subprocess_exec`,
which on Windows needs the Proactor event loop — the default policy since
Python 3.8. Code that has installed `WindowsSelectorEventLoopPolicy` (a
leftover from older `asyncio`-plus-`select`-based libraries) makes that call
raise `NotImplementedError` instead of spawning anything; there is no SDK
workaround, because subprocess creation on Windows is Proactor-only.

!!! warning "Windows: a documented degraded path"
    `capture=False` passthrough deliberately does **not** set
    `CREATE_NEW_PROCESS_GROUP` on Windows, so `Ctrl-C` reaches the child
    naturally — `CTRL_C_EVENT` cannot be scoped to a process group; it
    broadcasts. The tradeoff: timeout enforcement on Windows falls back to
    `TerminateProcess` only, with no graceful phase. `capture=False`
    together with a `timeout` on Windows is a documented degraded
    combination, not an oversight — the two goals (SIGINT forwarding, a
    graceful kill ladder) genuinely conflict there.

## Output encoding

ocx is a Rust binary, so its own stdout and stderr are always valid UTF-8 —
the SDK decodes captured output with `.decode("utf-8", "replace")` and
never sniffs a locale. That guarantee covers only what ocx itself writes: a
hosted child started through `run`, `exec`, or `spawn` that writes bytes in
the host's console codepage (a non-UTF-8 Windows console, say) degrades to
`�` replacement characters wherever the SDK captures its output. `capture=False`
sidesteps this entirely — stdio passes through to the terminal raw, never
decoded by the SDK.

`spawn`/`spawn_async` carry no SDK timeout at all — once you have the live
`Popen` / `Process`, waiting, killing, and pipe-draining are entirely yours,
exactly as with a bare `Popen`. Orphaned children are a stated non-goal in
v0.1: nothing here installs `PR_SET_PDEATHSIG` or a Windows Job Object, so a
`spawn`ed child of a `SIGKILL`'d parent keeps running.

## Scoping configuration: `with_config`

[`Ocx.with_config(**overrides)`](../../reference/api.md#ocx_sdk.Ocx.with_config)
(and its `Project` twin) derives a handle that shares the parent's binary,
host environment, `on_log`, and compatibility memo — only the named
`OcxConfig` fields differ. Nothing about deriving a handle re-runs
discovery, so a `with_config` chain can never end up pointing at a
different binary than the one it started from. Anything other than
configuration — a different binary, a different host environment — means
constructing a fresh `Ocx`, not deriving one.

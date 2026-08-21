# Errors & credentials

## The error model: exit code *is* the category

Every exception the SDK raises from a process failure derives from the exit
code alone — never from matching stderr text, which is exactly the kind of
heuristic that breaks quietly when a message's wording changes upstream.
[`OcxError`](../../reference/api.md#ocx_sdk.OcxError) is the root; every
subclass's `__str__` carries an actionable next step, not just a fact,
because the message is what shows up in a CI log and a bare fact leaves the
reader guessing what to do about it.

```python
from ocx_sdk import DataError

error = DataError(65, ["ocx", "add", "--bad-flag"], stderr="unexpected identifier")
assert "next step" not in str(error)  # every message names one; this just isn't it verbatim
assert "stderr:" in str(error)
```

Two catch shapes cover almost everything:

- **`except OcxExecutionError`** — a process that failed *and* one that
  never finished (a non-zero exit and a timeout share this parent), the
  shape most callers actually want.
- **`except OcxProcessError`** — a non-zero exit specifically, with
  `.exit_code` (a plain `int`, because a signal-killed process exits with a
  code ocx never assigns — `137` for `SIGKILL`, say — and building an error
  object must never itself raise) and `.retryable`, which reports what ocx
  said about the failure (fixed: `True` only for exit `75`), independent of
  whatever `RetryPolicy` a caller happened to pass.

The full exit-code-to-exception table, and which codes retry by default,
lives on [Environment & exit codes](../../reference/environment.md#exit-codes).
`BootstrapError` and its children (`DownloadError`,
`ChecksumMismatchError`, `DistManifestError`, `UnsupportedPlatformError`)
are a separate, non-process branch under `OcxError` — failures while
resolving, downloading, or installing a binary, before any typed command
ever runs.

## `PackageRef`: carried, never parsed

ocx owns the package-identifier grammar. [`PackageRef`](../../reference/api.md#ocx_sdk.PackageRef)
stores whatever identifier string it was given and hands it back byte for
byte — the SDK never re-derives or re-validates the grammar itself:

```python
from ocx_sdk import PackageRef

ref = PackageRef("ocx.sh/astral-sh/uv:0.9.7")
carried_forward = PackageRef("ocx.sh/astral-sh/uv:0.9.7", metadata={"source": "lock"})

assert str(ref) == "ocx.sh/astral-sh/uv:0.9.7"
assert ref == carried_forward  # identity is the identifier alone; metadata is decoration
```

Identity is the identifier string alone — `metadata` is whatever context
came along with the ref from the JSON row that produced it, so two refs to
the same package parsed out of two different commands must compare equal
even when their metadata differs. Any parameter typed `PackageLike` accepts
either a bare `str` or a `PackageRef` and coerces with `str()`.

## Credential handling

One vocabulary for both bootstrap and runtime auth:
[`BasicAuth(user, password)`](../../reference/api.md#ocx_sdk.BasicAuth) and
[`BearerAuth(token)`](../../reference/api.md#ocx_sdk.BearerAuth) — `None`
elsewhere means anonymous, matching ocx's own type set. Their secret fields
are `field(repr=False)`, with a hand-written `__repr__` that masks the
value — the dataclass-generated repr would otherwise leak into logs,
tracebacks, and pytest diffs:

```python
from ocx_sdk import BasicAuth, BearerAuth

assert repr(BasicAuth("ci", "hunter2")) == "BasicAuth(user='ci', password=***)"
assert repr(BearerAuth("ghp_secret")) == "BearerAuth(token=***)"
```

Beyond the repr mask, every secret value the SDK composed into a spawn
environment is exact-string-redacted from captured stderr, `on_log` lines,
logged argv, and exception text — the choke point is
[`_env.build_spawn_env()`](../../reference/environment.md#auth-ocx_auth_slug_), which
returns the finished environment paired with a `redact` callable that
`_process` applies to every outbound surface before it leaves the SDK. A
caller who puts a token into `invoke`'s raw argv gets it scrubbed the same
way, even though doing so is documented against.

One caveat the redaction can't reach: at `log_level="trace"`, ocx itself may
print secrets it holds that never passed through the SDK's own composition
— the scrub only catches values the SDK was given.

### Blast radius under `run` and `exec`

ocx does not scrub non-forwarded environment variables from a spawned
child — so a tool started through
[`Project.run`](../../reference/api.md#ocx_sdk.Project.run) or
[`package.exec`](../../reference/api.md#ocx_sdk.PackageCommands.exec)
inherits `OCX_AUTH_*`, whatever set it to (ambient environment or explicit
`OcxConfig.auth`). This is pinned by a contract test against the real
binary specifically so an upstream change to that behavior breaks loudly
rather than silently. The credential-free pattern: authenticate for the
`pull()`, then run the build step through a config with credentials
cleared —

```python-no-run
# illustrative: needs a real Project handle.
project.pull()                                       # needs the token
project.with_config(auth={}).run(["task", "build"])   # the build step doesn't see it
```

— covered in full, with the rest of the hermetic-CI threat model, in
[Hermetic CI](../hermetic-ci.md#the-one-thing-hardening-does-not-cover-ocx_auth_-under-run).

### Persistent credentials

[`Ocx.login`](../../reference/api.md#ocx_sdk.Ocx.login) writes credentials
through ocx's own store — the token travels on stdin via
`--password-stdin`, never in argv, and is redacted for the call's duration
like any other secret. `OcxConfig.docker_config` points ocx at an isolated
credential store directory; keep it `0700`, since it holds registry
credentials on disk.

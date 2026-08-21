# Environment & exit codes

Every variable this SDK reads or writes, and how an ocx exit code becomes a
Python exception. The composition itself happens in one place —
`_env.build_spawn_env()` — described conceptually in
[Errors & credentials](../guide/concepts/errors-and-security.md); this page
is the exhaustive wire-level table.

## Neutralized on every spawn

Dropped from the ambient environment before anything else, regardless of
configuration — an inherited value here would silently retarget or
mis-scope a call.

| Variable | Why |
|---|---|
| `OCX_PROJECT` | The SDK always targets a project through an explicit `--project`. |
| `OCX_GLOBAL` | Same reasoning — global scope is explicit, never ambient. |
| `OCX_QUIET` | The SDK controls output verbosity through its own presentation flags. |

## Written from `OcxConfig`

`_env.build_spawn_env()` maps `OcxConfig` fields onto these. A plain `bool`
field is set-only — `False` means "not requested", and the host's ambient
value (if any) survives. A field typed `X | None` can actively clear an
ambient value; `None` means "leave the host's value alone."

| Variable | `OcxConfig` field | Notes |
|---|---|---|
| `OCX_OFFLINE` | `offline: bool` | Set to `1` when `True`; otherwise unwritten. |
| `OCX_FROZEN` | `frozen: bool` | Same set-only shape. |
| `OCX_NO_CONFIG` | `no_config: bool` | Same set-only shape. |
| `OCX_NO_UPDATE_CHECK` | `no_update_check: bool` | The one field always written (`"1"` or `"0"`) — its SDK default is `True`, so a caller asking for the update check back has to be able to beat an ambient `OCX_NO_UPDATE_CHECK=1`. |
| `OCX_HOME` | `home: Path \| None` | |
| `OCX_CONFIG` | `config: Path \| None` | |
| `OCX_INDEX` | `index: Path \| None` | |
| `DOCKER_CONFIG` | `docker_config: Path \| None` | Keep the directory `0700` — it holds registry credentials. |
| `OCX_JOBS` | `jobs: int \| None` | |
| `OCX_MIRRORS` | `mirrors: Mapping[str, str] \| None` | Serialized as JSON. |
| `OCX_MANAGED_CONFIG` | `managed_config: str \| None` | `MANAGED_CONFIG_DISABLED` (`""`) force-disables an ambient managed-config tier; skipped entirely under `no_config`. |
| `OCX_NO_CONFIG_REFRESH` | `no_config_refresh: bool \| None` | `True` writes `1`; `False` explicitly clears an ambient value; `None` leaves it alone. |
| `OCX_INSECURE_REGISTRIES` | `insecure_registries: Collection[str] \| None` | **Fail-closed**: any explicit value, including `()`, replaces the ambient set entirely rather than merging with it. |

## Auth — `OCX_AUTH_<SLUG>_*`

For every registry in `OcxConfig.auth`, the SDK writes (and first clears the
full existing triple for) the slug ocx's own `registry_slug` canonicalizes
to:

| Variable | Value |
|---|---|
| `OCX_AUTH_<SLUG>_TYPE` | `basic` or `token` |
| `OCX_AUTH_<SLUG>_USER` | Only for `BasicAuth`. |
| `OCX_AUTH_<SLUG>_TOKEN` | The password (`BasicAuth`) or bearer token (`BearerAuth`). |

`<SLUG>` is every character outside `[A-Za-z0-9]` in the registry name,
replaced with `_` — `ghcr.io` becomes `GHCR_IO`. An ambient `OCX_AUTH_*` for
a registry **not** named in `config.auth` passes through untouched; explicit
configuration only overrides its own slug. Two registries that canonicalize
to the same slug, or a registry that canonicalizes to an empty slug, raise
`OcxError` rather than silently dropping or colliding credentials.

**Propagation**: ocx does not scrub non-forwarded variables from a spawned
child's environment, so a tool started through `Project.run` or
`package.exec` inherits `OCX_AUTH_*`. See
[Errors & credentials](../guide/concepts/errors-and-security.md) for the
credential-free pattern.

## Bootstrap-only — `OCX_INSTALL_*`

Read by [`bootstrap.ensure`](api.md#ocx_sdk.ensure), one rung below
its explicit keyword arguments and one above its own defaults. Never written
by the SDK.

| Variable | `ensure()` argument |
|---|---|
| `OCX_INSTALL_VERSION` | `version` |
| `OCX_INSTALL_DIST_URL` | consulted by the *default* `DistSource` only — an explicitly constructed one does not honor it |
| `OCX_INSTALL_MIRROR_URL` | `mirror_url` |
| `OCX_INSTALL_REPO` | **No-op.** Listed for grammar parity with the setup script's `OCX_INSTALL_*` vars only — this SDK resolves artifact URLs from the manifest, never from a GitHub repository guess. |
| `OCX_INSTALL_FORCE` | forces a fresh install even on a cache hit |
| `OCX_INSTALL_QUIET` | **No-op.** Listed for grammar parity only — this module never prints, so there is nothing to quiet. |

## Discovery

`bootstrap.discover` (used internally by `Ocx()` construction) resolves a
binary in this order:

1. An explicit `exe=` argument.
2. `OCX_SDK_EXE`.
3. `PATH` (the current working directory is excluded from the search on
   Windows).
4. `$OCX_HOME/…/current/content/bin/ocx` — ocx's own stable install symlink.

Nothing found raises `OcxNotFoundError`, whose message names
`bootstrap.ensure()` as the fix.

## Reserved for `[env]` entries

[`Project.env`](api.md#ocx_sdk.Project.env), [`Project.run`](api.md#ocx_sdk.Project.run),
and [`PackageCommands.test`](api.md#ocx_sdk.PackageCommands.test) accept extra
`env=` entries serialized as ocx's `--env KEY[:TYPE[:SEP]]=VALUE` flag. A key
in the `OCX_*` or `__OCX_*` namespace is rejected with `OcxError` — a project
cannot reconfigure how ocx itself resolves through this path. Set the
corresponding `OcxConfig` field instead.

## Exit codes

The exit code of the ocx process *is* the error category — `_process` maps
it to a subclass, and nothing in the SDK ever classifies a failure by
matching stderr text.

| Code | `ExitCode` | Exception | Retried by default? |
|---|---|---|---|
| 0 | `OK` | — | — |
| 1 | `FAILURE` | plain `OcxProcessError` | no |
| 64 | `USAGE` | `UsageError` | no |
| 65 | `DATA_ERR` | `DataError` | no |
| 69 | `UNAVAILABLE` | `UnavailableError` | no — ocx classified it non-transient |
| 74 | `IO_ERR` | `IoError` | no |
| 75 | `TEMP_FAIL` | `TempFailError` | **yes** — the only retry signal (`RetryPolicy.retry_on` default) |
| 77 | `NO_PERM` | `PermissionDeniedError` | no |
| 78 | `CONFIG` | `ConfigError` | no |
| 79 | `NOT_FOUND` | `NotFoundError` | no |
| 80 | `AUTH` | `AuthError` | no — auth failures are never retried |
| 81 | `POLICY_BLOCKED` | `PolicyBlockedError` | no |
| 82 | `DIRTY_RC_BLOCK` | `DirtyRcBlockError` | no |
| — (timeout, no exit code) | — | `OcxTimeoutError` | no |

A process killed by a signal exits with a code ocx never assigns (137 for
`SIGKILL`, for instance); `OcxProcessError.exit_code` is a plain `int` for
exactly that reason, and such an exit lands as the generic `OcxProcessError`
rather than any per-code subclass. `except OcxExecutionError` is the catch
shape that covers both a non-zero exit and a timeout in one clause.

Two methods default their per-call `retry` to `None` regardless of session
policy: [`Ocx.login`](api.md#ocx_sdk.Ocx.login) and
[`package.push`](api.md#ocx_sdk.PackageCommands.push) — see
[Concurrency & timeouts](../guide/concepts/concurrency.md#retries) for why,
and pass `retry=` explicitly to override.

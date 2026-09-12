# Environment & exit codes

Every variable this SDK reads or writes, and how an ocx exit code becomes a
Python exception. The composition itself happens in one place —
`_env.build_spawn_env()` — described conceptually in
[Errors & credentials](../guide/concepts/errors-and-security.md); this page
is the exhaustive wire-level table.

## Neutralized on every spawn

Dropped from the ambient environment before anything else, regardless of
configuration — an inherited value here would silently retarget or
mis-scope a call. The drop is **case-insensitive**: `ocx_no_verify` goes
the same way `OCX_NO_VERIFY` does, because a Windows child resolves an
environment lookup case-insensitively and would read either spelling.

| Variable | Why |
|---|---|
| `OCX_PROJECT` | The SDK always targets a project through an explicit `--project`. |
| `OCX_GLOBAL` | Same reasoning — global scope is explicit, never ambient. |
| `OCX_QUIET` | The SDK controls output verbosity through its own presentation flags. |
| `OCX_NO_VERIFY` | A silent kill switch for signature verification — a skipped verification looks identical to a passed one. Say it at the call site instead: `install(..., verify=False)` / `pull(..., verify=False)`, whose argv flag outranks the variable. |
| `OCX_NO_HOOK` | Governs the per-prompt shell hook, which a spawned child never renders. |
| `OCX_NO_COMPLETIONS` | Its sibling in the same ladder, for the same reason. |

`OCX_SIGSTORE_TRUSTED_ROOT` is cleared too, one step later — it is popped by
the config pass rather than the neutralization pass, because
`OcxConfig.sigstore_trusted_root` can put a value back. It redefines what
"trusted" means and outranks `[trust.sigstore]`, so an inherited value could
repoint Fulcio, CT, and Rekor while every install still reported *verified*.

## Written from `OcxConfig`

`_env.build_spawn_env()` maps `OcxConfig` fields onto these. A plain `bool`
field is set-only — `False` means "not requested", and the host's ambient
value (if any) survives. A field typed `X | None` can actively clear an
ambient value; `None` means "leave the host's value alone" — except for the
three rows marked *always written*, `OCX_NO_UPDATE_CHECK`, `OCX_NO_CONSENT`
and `OCX_SIGSTORE_TRUSTED_ROOT`, where saying nothing has to mean the SDK's
default rather than whatever the host exported.

Every value the SDK writes here — and every ambient value it clears —
replaces the host's answer for that name in **any case spelling**, for the
same reason the neutralization table gives. A field the SDK does *not*
write leaves the ambient value exactly as the host spelled it.

| Variable | `OcxConfig` field | Notes |
|---|---|---|
| `OCX_OFFLINE` | `offline: bool` | Set to `1` when `True`; otherwise unwritten. |
| `OCX_FROZEN` | `frozen: bool` | Same set-only shape. |
| `OCX_NO_CONFIG` | `no_config: bool` | Same set-only shape. |
| `OCX_NO_UPDATE_CHECK` | `no_update_check: bool` | *Always written* (`"1"` or `"0"`) — its SDK default is `True`, so a caller asking for the update check back has to be able to beat an ambient `OCX_NO_UPDATE_CHECK=1`. |
| `OCX_NO_CONSENT` | `consent: bool` | *Always written* (`"1"` unless `consent=True`). Without it every `add`/`lock`/`pull`/`exec`/`update`/`init` stamps `state/projects/<key>/consent.json`, which is what lets a shell prompt in that directory activate the project — a side effect a program step should not leave behind. `ocx exec` forwards it to nested ocx. This is the per-project stamp, not the shell-activation consent `ocx shell allow` records. |
| `OCX_HOME` | `home: Path \| None` | |
| `OCX_CONFIG` | `config: Path \| None` | |
| `OCX_INDEX` | `index: Path \| None` | |
| `DOCKER_CONFIG` | `docker_config: Path \| None` | Keep the directory `0700` — it holds registry credentials. |
| `OCX_JOBS` | `jobs: int \| None` | |
| `OCX_MIRRORS` | `mirrors: Mapping[str, str] \| None` | Serialized as JSON. |
| `OCX_MANAGED_CONFIG` | `managed_config: str \| None` | `MANAGED_CONFIG_DISABLED` (`""`) force-disables an ambient managed-config tier; skipped entirely under `no_config`. |
| `OCX_NO_CONFIG_REFRESH` | `no_config_refresh: bool \| None` | `True` writes `1`; `False` explicitly clears an ambient value; `None` leaves it alone. |
| `OCX_INSECURE_REGISTRIES` | `insecure_registries: Collection[str] \| None` | **Fail-closed**: any explicit value, including `()`, replaces the ambient set entirely rather than merging with it. |
| `OCX_SIGSTORE_TRUSTED_ROOT` | `sigstore_trusted_root: str \| Path \| None` | *Always written*: a value sets it, `None` **pops** any ambient one. `None` therefore means ocx's own root of trust, not the host's — see the neutralization table for why. Typed `str \| Path` because CI configuration usually interpolates it as text. |
| `OCX_RECORDS_DIR` | `records_dir: Path \| None` | Where `exec` writes its execution record; the directory must already exist. The per-call `records_dir=` on every `exec`/`spawn` verb overrides it. |
| `OCX_RECORDS_NAME` | `records_name: str \| None` | The record's filename template (`{time}`, `{host}`, `{pid}`, `{rand}`). Per-call `records_name=` overrides it. |
| `OCX_TOOLCHAIN_DIR` | `toolchain_dir: Path \| None` | Where the project toolchain links live. `OCX_TOOLCHAIN_PINNED` and `OCX_TOOLCHAIN_ACTIVATE` are deliberately not modelled — the former is the weakest tier under `pinned=` on `env`/`exec`, the latter is a shell-session concern. |
| `OCX_ANNOUNCE_TOKEN` | `forge_token: str \| None` | The forge API credential `announce`/`claim` use — the top rung of the [identity ladder](../guide/hermetic-ci.md#the-forge-identity-ladder). `repr=False`; redacted. |
| `OCX_ANNOUNCE_GIT_TOKEN` | `forge_git_token: str \| None` | The push-leg credential for `transport="git"`. `repr=False`; redacted. |
| `OCX_ANNOUNCE_GIT_USERNAME` | `forge_git_username: str \| None` | The username the git push leg authenticates as. |

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
replaced with `_`, **case preserved** — `ghcr.io` becomes `ghcr_io`, so the
SDK writes `OCX_AUTH_ghcr_io_TOKEN`. ocx's own `to_slug` does not case-fold
and neither does this. An ambient `OCX_AUTH_*` for
a registry **not** named in `config.auth` passes through untouched; explicit
configuration only overrides its own slug. The clear is case-insensitive
across the whole name, so a configured `ghcr.io` also removes an ambient
`ocx_auth_ghcr_io_*` rather than shipping two credential sets for one
registry. Two registries that canonicalize to the same slug, or a registry
that canonicalizes to an empty slug, raise `OcxError` rather than silently
dropping or colliding credentials.

Credentials the *host* exported are redacted from logs and error text
alongside the ones the SDK wrote, in whatever case they were spelled. The
forge rungs — `OCX_ANNOUNCE_TOKEN`, `OCX_ANNOUNCE_GIT_TOKEN`, `CI_JOB_TOKEN`
— are redacted the same way, ambient or configured.

**Propagation**: ocx does not scrub non-forwarded variables from a spawned
child's environment, so a tool started through `Project.exec` or
`package.exec` inherits `OCX_AUTH_*`. See
[Errors & credentials](../guide/concepts/errors-and-security.md) for the
credential-free pattern.

## Bootstrap-only — `OCX_INSTALL_*`

Read by [`bootstrap.ensure`](api.md#ocx_sdk.ensure), one rung below
its explicit keyword arguments and one above its own defaults. Never written
by the SDK.

This is the same `OCX_INSTALL_*` grammar
`setup.ocx.sh` defines, so an environment already
configured for the shell installer needs no code change here — export the
variable and `bootstrap.ensure()` picks it up. The two exceptions are called
out as **No-op** below, and the setup script's remaining variables
(`OCX_INSTALL_NO_SETUP`, `OCX_INSTALL_NO_SMOKETEST`, `OCX_INSTALL_PRINT_PATH`,
`OCX_INSTALL_DOWNLOADER`, `OCX_NO_MODIFY_PATH`) have no meaning here: this SDK
never runs setup, never prints, never modifies `PATH`, and has no
curl-or-wget choice to make.

| Variable | `ensure()` argument |
|---|---|
| `OCX_INSTALL_VERSION` | `version` — pins an exact release. Empty or unset takes the channel's latest. |
| `OCX_INSTALL_DIST_URL` | consulted by the *default* `DistSource` only — an explicitly constructed one does not honor it |
| `OCX_INSTALL_MIRROR_URL` | `mirror_url` — relocates the artifact host, as `<mirror_url>/<tag>/<filename>`. The manifest digest is still enforced, so a mirror moves bytes and never revalidates them; the manifest itself keeps coming from `dist`. Setting this makes the manifest `sha256=` mandatory — see [the off-canonical rule](../guide/bootstrap.md#the-sha256-off-canonical-rule). |
| `OCX_INSTALL_CA_BUNDLE` | `ca_bundle` — a PEM file trusted for the manifest and artifact downloads *instead of* the system store, for a TLS-intercepting proxy. Transport trust only: the manifest pin and the artifact digest are still enforced, so the bundle changes who may serve the bytes, never which bytes are accepted. |
| `OCX_INSTALL_REPO` | **No-op.** Listed for grammar parity with the setup script's `OCX_INSTALL_*` vars only — this SDK resolves artifact URLs from the manifest, never from a GitHub repository guess. |
| `OCX_INSTALL_FORCE` | no argument — forces a fresh download and install even when the cache already holds a correct binary |
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

[`Project.env`](api.md#ocx_sdk.Project.env), [`Project.exec`](api.md#ocx_sdk.Project.exec),
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
| 83 | `TRANSPARENCY_LOG_UNAVAILABLE` | `TransparencyLogUnavailableError` | no — retrying amplifies Rekor's rate limiting, and a later success is not restored trust |
| 84 | `REFERRERS_UNSUPPORTED` | `ReferrersUnsupportedError` | no |
| 85 | `UNSUPPORTED_KEY_BACKEND` | `UnsupportedKeyBackendError` | no |
| 86 | `FORGE_CAPABILITY_UNAVAILABLE` | `ForgeCapabilityUnavailableError` | no — the credential is valid and the forge was reached; an administrator has to enable job-token push on the target project or allowlist the publishing one |
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

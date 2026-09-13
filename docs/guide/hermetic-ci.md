# Hermetic CI

Three defaults, each individually reasonable for a dev library, add up to a
specific fact worth stating plainly: **the ambient environment is a trusted
input** unless you turn it off.

- `OCX_INSTALL_*` is honored from the ambient environment by
  `bootstrap.ensure()` — it chooses what gets downloaded and from where.
- `OCX_AUTH_*` passes through to a spawned ocx untouched — it chooses which
  credentials attach to a registry call.
- Binary discovery walks `PATH` — it chooses which `ocx` actually runs.

None of this is a bug; a CI runner's ambient environment usually *is*
trustworthy. But a build that wants to say so explicitly, or one running in
an environment it does not fully trust, has levers for every one of the
three — all opt-in, all composable.

## The levers

| Trust boundary | Default | Hardening lever |
|---|---|---|
| What `bootstrap.ensure()` downloads | Ambient `OCX_INSTALL_*` honored ([which ones](../reference/environment.md#bootstrap-only-ocx_install_)) | Pass `version=`, `dist=`, `mirror_url=`, `ca_bundle=` explicitly; construct `DistSource` with an explicit `sha256=` |
| Which credentials a spawn carries | Ambient `OCX_AUTH_*` passes through | `Ocx(host_env=HostEnv.clean())` or `.without(...)`; explicit `OcxConfig.auth` always wins over ambient for the same registry |
| Which binary runs | `PATH` search | `Ocx(exe=...)` — the hardened form: it trusts a location without inspecting how it was reached |
| Registry transport | Whatever `insecure_registries` the ambient env allows | `OcxConfig(insecure_registries=())` — **fail-closed**: an explicit value, including the empty tuple, replaces the ambient set entirely rather than merging with it |
| Cache integrity | Re-hashed on every cache hit | Already default-on; `trust_cache=True` is the (explicit) way to opt back out |

Explicit configuration always wins over the ambient environment — that
precedence is uniform across every lever above, not something to remember
per field.

```python
from ocx_sdk import OcxConfig

hermetic = OcxConfig(insecure_registries=())
assert hermetic.insecure_registries == ()
```

A CI image that exports `OCX_INSECURE_REGISTRIES` cannot re-enable
plaintext through this config — the empty tuple is the fail-closed answer,
not the same as leaving `insecure_registries` unset (`None`, which inherits
whatever the ambient environment allows).

## A recipe

```python-no-run
# illustrative: needs a real binary; substitute your discovery/pin strategy.
from ocx_sdk import HostEnv, Ocx, OcxConfig, bootstrap

ocx = Ocx(
    exe=bootstrap.ensure(version="0.6.2"),        # pinned, not "latest"
    host_env=HostEnv.minimal(),                   # PATH/HOME/TMPDIR only
    config=OcxConfig(
        insecure_registries=(),                   # fail-closed
        no_config=True,                           # ignore any discovered config.toml
    ),
)
```

`HostEnv.minimal()` rather than `HostEnv.clean()` here: a spawned toolchain
still needs `PATH` to find its own dependencies, and `clean()` drops it —
see [Bootstrap](bootstrap.md#hostenv-tiers) for the full tier list.

## What a spawn leaves behind: the consent stamp

One default runs the other way — it is on regardless of the levers above.
ocx stamps `state/projects/<key>/consent.json` on every `add`, `lock`,
`pull`, `exec`, `update` and `init`, and the stamp is what lets a shell
prompt in that directory activate the project at the developer's next
`cd`. A program step should not leave that behind, so the SDK writes
`OCX_NO_CONSENT=1` on every spawn (`ocx exec` forwards it to nested ocx) and
an ambient `OCX_NO_CONSENT=0` cannot switch it back. A pipeline that does
want the project live afterwards says so on the handle:

```python
from ocx_sdk import OcxConfig

assert OcxConfig().consent is False
consenting = OcxConfig(consent=True)  # `handle.with_config(consent=True)` in practice
assert consenting.consent is True
```

That is the per-project stamp only; the shell-activation consent
`ocx shell allow` records is not wrapped.

## The forge identity ladder

[`announce`](../reference/api.md#ocx_sdk.PackageCommands.announce) and
[`claim`](../reference/api.md#ocx_sdk.PackageCommands.claim) write to a
forge, not a registry, and ocx resolves *that* identity from a ladder of
ambient variables: `OCX_ANNOUNCE_TOKEN` first; on GitLab, `CI_JOB_TOKEN`
(when `GITLAB_CI` is set and the transport is `git`); `OCX_ANNOUNCE_GIT_TOKEN`
and `OCX_ANNOUNCE_GIT_USERNAME` for the push leg; `CI_PROJECT_PATH`,
`GITLAB_USER_LOGIN`/`GITLAB_USER_ID` or `GITHUB_ACTOR`/`GITHUB_ACTOR_ID` for
the owner identity a claim records.

`HostEnv.minimal()` drops every rung. A hermetic handle therefore announces
with `credential_kind: "none"` and fails with `AuthError` (80) before any
network — the right default for a step that was not meant to publish. The
step that *is* meant to publish says so on the handle, either by letting the
named rungs through or by configuring the credential explicitly:

```python
from ocx_sdk import HostEnv, OcxConfig

runner = HostEnv({"PATH": "/usr/bin", "GITLAB_CI": "true", "CI_JOB_TOKEN": "glcbt-...", "OCX_AUTH_X_TOKEN": "t"})
let_ci_through = runner.only("PATH", "HOME", "TMPDIR", "GITLAB_CI", "CI_JOB_TOKEN", "CI_PROJECT_PATH")
assert set(let_ci_through.source) == {"PATH", "GITLAB_CI", "CI_JOB_TOKEN"}  # `.ambient().only(...)` in practice

explicit = OcxConfig(forge_token="glpat-secret")
assert "glpat" not in repr(explicit)
```

`OcxConfig.forge_token`, `forge_git_token` and `forge_git_username` reach
the child as `OCX_ANNOUNCE_TOKEN`, `OCX_ANNOUNCE_GIT_TOKEN` and
`OCX_ANNOUNCE_GIT_USERNAME`, explicit winning over ambient like every other
lever. The three token values — ambient or configured — are redacted from
stderr, `on_log`, logged argv and exception text exactly as `OCX_AUTH_*` is.
Exit 86 ([`ForgeCapabilityUnavailableError`](../reference/api.md#ocx_sdk.ForgeCapabilityUnavailableError))
is the one forge failure a credential change cannot fix: the token is valid
but the target project does not allow job-token push, and only an
administrator's allowlist entry changes that.

## The one thing hardening does not cover: `OCX_AUTH_*` under `exec`

ocx does not scrub non-forwarded variables from a spawned child's
environment — "non-forwarded is not the same as scrubbed." That means a
tool started through
[`Project.exec`](../reference/api.md#ocx_sdk.Project.exec) or
[`package.exec`](../reference/api.md#ocx_sdk.PackageCommands.exec)
**inherits whatever `OCX_AUTH_*` the handle's environment carries**, whether
that came from ambient env or explicit `OcxConfig.auth`. This is ocx's
behavior, not the SDK's, and hardening the levers above does not change it
by itself — `HostEnv.clean()`/`.minimal()` does, because it drops the
ambient `OCX_AUTH_*` before the SDK ever sees it, but an explicit
`OcxConfig.auth` you configured is deliberately still there for ocx itself
to use.

The credential-free pattern for a step that should not see the token at
all: pull first, authenticated, then run through a config with the
credentials cleared.

```python-no-run
# illustrative: needs a real Project handle.
project.pull()                              # authenticated — needs the token
project.with_config(auth={}).exec(["task", "build"])  # the build step does not
```

See [Errors & credentials](concepts/errors-and-security.md) for the full
credential-handling picture, including why secrets never appear in a repr,
a log line, or an exception message even when they do reach the child
process.

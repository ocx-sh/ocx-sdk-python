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
| What `bootstrap.ensure()` downloads | Ambient `OCX_INSTALL_*` honored | Pass `version=`, `dist=`, `mirror_url=` explicitly; construct `DistSource` with an explicit `sha256=` |
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
    exe=bootstrap.ensure(version="0.5.8"),        # pinned, not "latest"
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

## The one thing hardening does not cover: `OCX_AUTH_*` under `run`

ocx does not scrub non-forwarded variables from a spawned child's
environment — "non-forwarded is not the same as scrubbed." That means a
tool started through
[`Project.run`](../reference/api.md#ocx_sdk.Project.run) or
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
project.with_config(auth={}).run(["task", "build"])   # the build step does not
```

See [Errors & credentials](concepts/errors-and-security.md) for the full
credential-handling picture, including why secrets never appear in a repr,
a log line, or an exception message even when they do reach the child
process.

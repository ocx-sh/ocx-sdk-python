# Command ↔ method map

Every typed method mirrors one ocx command. **T1** commands are typed and
CI-covered; **T2** commands are real but not yet typed (reach them through
[`invoke`](api.md#ocx_sdk.Ocx.invoke) or [`invoke_async`](api.md#ocx_sdk.Ocx.invoke_async));
**T3** commands are interactive, shell-session, or not-a-frozen-contract, and
stay `invoke`-only by design; **✗** commands are never wrapped.

Full flag fidelity is scoped to stable, resolution-affecting flags —
experimental surfaces (`--ci=gitlab` and similar) are excluded on purpose.

## Machine tier — `Ocx`

No project path; operates on `$OCX_HOME`.

| ocx command | SDK method | Tier |
|---|---|---|
| `ocx version` | [`Ocx.version`](api.md#ocx_sdk.Ocx.version) | T1 |
| `ocx about` | [`Ocx.about`](api.md#ocx_sdk.Ocx.about) | T1 |
| `ocx login` | [`Ocx.login`](api.md#ocx_sdk.Ocx.login) | T1 |
| `ocx logout` | [`Ocx.logout`](api.md#ocx_sdk.Ocx.logout) | T1 |
| `ocx status` | [`Ocx.project(path).status`](api.md#ocx_sdk.Project.status) | T1 |
| `ocx clean` | — | T2 |
| any command | [`Ocx.invoke`](api.md#ocx_sdk.Ocx.invoke) / [`invoke_async`](api.md#ocx_sdk.Ocx.invoke_async) / [`spawn`](api.md#ocx_sdk.Ocx.spawn) / [`spawn_async`](api.md#ocx_sdk.Ocx.spawn_async) | raw escape hatch |
| `shell hook` / `shell env` / `shell init`, `ci*`, bare aliases | — | ✗ dead stubs |
| `direnv*` | — | T3, needs human `direnv allow` |
| `self activate`, `shell completion` | — | T3, shell-session only |

## Project tier — `Ocx.project(path)`

Every call carries `--project <path>` explicitly, so no method reads the
working directory.

| ocx command | SDK method | Tier |
|---|---|---|
| `ocx init --project` | [`Project.init`](api.md#ocx_sdk.Project.init) | T1 |
| `ocx add --project` | [`Project.add`](api.md#ocx_sdk.Project.add) | T1 |
| `ocx remove --project` | [`Project.remove`](api.md#ocx_sdk.Project.remove) | T1 |
| `ocx lock --project` | [`Project.lock`](api.md#ocx_sdk.Project.lock) | T1 |
| `ocx update --project` | [`Project.update`](api.md#ocx_sdk.Project.update) | T1 |
| `ocx pull --project` | [`Project.pull`](api.md#ocx_sdk.Project.pull) | T1 |
| `ocx status --project` | [`Project.status`](api.md#ocx_sdk.Project.status) | T1 |
| `ocx inspect --project` | [`Project.inspect`](api.md#ocx_sdk.Project.inspect) | T1 |
| `ocx env --project` | [`Project.env`](api.md#ocx_sdk.Project.env) | T1 |
| `ocx run --project -- CMD` | [`Project.run`](api.md#ocx_sdk.Project.run) / [`run_async`](api.md#ocx_sdk.Project.run_async) / [`spawn`](api.md#ocx_sdk.Project.spawn) / [`spawn_async`](api.md#ocx_sdk.Project.spawn_async) | T1 |

## Package tier — `Ocx.package`

Consumer surface: installing and running packages from `$OCX_HOME`, no
project path.

| ocx command | SDK method | Tier |
|---|---|---|
| `ocx package install` | [`package.install`](api.md#ocx_sdk.PackageCommands.install) | T1 |
| `ocx package select` | [`package.select`](api.md#ocx_sdk.PackageCommands.select) | T1 |
| `ocx package uninstall` | [`package.uninstall`](api.md#ocx_sdk.PackageCommands.uninstall) | T1 |
| `ocx package deselect` | [`package.deselect`](api.md#ocx_sdk.PackageCommands.deselect) | T1 |
| `ocx package env` | [`package.env`](api.md#ocx_sdk.PackageCommands.env) | T1 |
| `ocx package exec` | [`package.exec`](api.md#ocx_sdk.PackageCommands.exec) / [`exec_async`](api.md#ocx_sdk.PackageCommands.exec_async) / [`spawn`](api.md#ocx_sdk.PackageCommands.spawn) / [`spawn_async`](api.md#ocx_sdk.PackageCommands.spawn_async) | T1 |
| `ocx package which` | [`package.which`](api.md#ocx_sdk.PackageCommands.which) † | T1 |
| `ocx package inspect` | [`package.inspect`](api.md#ocx_sdk.PackageCommands.inspect) | T1 |
| `ocx package info` | [`package.info`](api.md#ocx_sdk.PackageCommands.info) | T1 |
| `ocx package deps` | [`package.deps`](api.md#ocx_sdk.PackageCommands.deps) | T1 |
| `ocx package pull` | [`package.pull`](api.md#ocx_sdk.PackageCommands.pull) | T1 |
| `ocx package create` | [`package.create`](api.md#ocx_sdk.PackageCommands.create) | T1, author flow |
| `ocx package test --script` | [`package.test`](api.md#ocx_sdk.PackageCommands.test) | T1, author flow — stable v1 JSON |
| `ocx package test -- CMD` | [`Ocx.invoke`](api.md#ocx_sdk.Ocx.invoke) | prints the child's raw stdout even under `--format json`; never parsed here |
| `ocx package push` | [`package.push`](api.md#ocx_sdk.PackageCommands.push) | T1, author flow |
| `ocx package describe`, `ocx package announce` | — | T2 |
| `ocx package cascade *` | — | T3, not a frozen wire contract |

† `package which`'s JSON is doc-flagged "breaking, pre-1.0" — typed, but not
on ocx's durable-anchor list. `EnvReport`'s four non-entry arrays carry the
same flag.

## Config tier — `Ocx.config`

The corporate managed-config surface.

| ocx command | SDK method | Tier |
|---|---|---|
| `ocx config setup` | [`config.setup`](api.md#ocx_sdk.ConfigCommands.setup) | T1 |
| `ocx config update` | [`config.update`](api.md#ocx_sdk.ConfigCommands.update) | T1 |
| `ocx config test`, `ocx config push` | — | T2 |

## Index tier

| ocx command | SDK method | Tier |
|---|---|---|
| `ocx index update/catalog/list/sync/regenerate` | — | T2, whole group |

## Patch tier — `Ocx.patch`

| ocx command | SDK method | Tier |
|---|---|---|
| `ocx patch sync` | [`patch.sync`](api.md#ocx_sdk.PatchCommands.sync) | T1 |
| `ocx patch publish/test/freeze/why` | — | T2 |

## Self tier

| ocx command | SDK method | Tier |
|---|---|---|
| `ocx self setup`, `ocx self update` | — | T2 |
| `ocx self activate` | — | T3, shell-session |

The SDK never calls `self setup`, `self activate`, or anything else that
mutates the machine outside `$OCX_HOME` — see
[bootstrap](../guide/bootstrap.md) for what `ensure()` does instead.

## Provisioning — not an ocx command

[`bootstrap.ensure`](api.md#ocx_sdk.ensure) is a Python-only layer
above the CLI: it resolves, downloads, verifies, and caches an ocx binary,
then returns its path. See [bootstrap](../guide/bootstrap.md).

# Setup

This repo dogfoods OCX. Install OCX once, then everything runs through
`ocx exec`.

```bash
curl -sSL https://setup.ocx.sh | sh
git clone https://github.com/ocx-sh/ocx-sdk-python
cd ocx-sdk-python
ocx exec -- task verify
```

OCX bootstraps:

- `task` (Taskfile v3) — task runner
- `uv` — Python package manager + script runner
- `git-cliff` — changelog generation

`uv` provisions Python 3.12+ and installs the optional `dev` and
`docs` dependency groups on demand.

## Optional: drop the `ocx exec --` prefix

Three ways, all ending at the same bare `task <name>` that CI runs — there,
`ocx-sh/setup-ocx` performs the identical activation.

**`ocx shell allow`** — no extra tooling. The installer already wired
`$OCX_HOME/env.sh` into your shell profile; this records the consent stamp
that lets a new prompt activate this project's toolchain:

```bash
ocx shell allow
task verify
```

Consent is per project and per source set: adding a tool from a registry the
stamp does not cover invalidates it, so run it again. `ocx shell state` says
why the integration is inert when it is, and `ocx shell revoke` withdraws the
stamp.

**[direnv](https://direnv.net)** — the repo ships an `.envrc`, so the project
activates whenever you `cd` into it, and re-activates on its own when
`ocx.toml` or `ocx.lock` changes:

```bash
direnv allow
task verify
task docs:serve
```

**One shell, by hand** — no stamp, no daemon, scoped to the current session:

```bash
eval "$(ocx env --shell=sh)"   # add to your shell profile if you like
task verify
```

## Tasks

| Task | Purpose |
|---|---|
| `task verify` | Full quality gate — format check, lint, types, tests, coverage |
| `task test` | pytest under coverage |
| `task lint` / `task types` | ruff / pyright alone |
| `task format` | apply the ruff formatter |
| `task docs:serve` / `task docs:build` | docs preview / strict build |
| `task changelog` | regenerate `CHANGELOG.md` from git history |

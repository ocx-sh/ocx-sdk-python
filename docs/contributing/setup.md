# Setup

This repo dogfoods OCX. Install OCX once, then everything runs through
`ocx run`.

```bash
curl -sSL https://setup.ocx.sh | sh
git clone https://github.com/ocx-sh/ocx-sdk-python
cd ocx-sdk-python
ocx run -- task verify
```

OCX bootstraps:

- `task` (Taskfile v3) — task runner
- `uv` — Python package manager + script runner
- `git-cliff` — changelog generation

`uv` provisions Python 3.12+ and installs the optional `dev` and
`docs` dependency groups on demand.

## Optional: drop the `ocx run --` prefix

The repo ships an `.envrc`, so with [direnv](https://direnv.net) the project
activates whenever you `cd` into it — and re-activates on its own when
`ocx.toml` or `ocx.lock` changes:

```bash
direnv allow
task verify
task docs:serve
```

Without direnv, activate the current shell by hand:

```bash
eval "$(ocx env --shell=sh)"   # add to your shell profile if you like
task verify
```

Either way you end up running the same bare `task <name>` that CI runs —
there, `ocx-sh/setup-ocx` performs the identical activation.

## Tasks

| Task | Purpose |
|---|---|
| `task verify` | Full quality gate — format check, lint, types, tests, coverage |
| `task test` | pytest under coverage |
| `task lint` / `task types` | ruff / pyright alone |
| `task format` | apply the ruff formatter |
| `task docs:serve` / `task docs:build` | docs preview / strict build |
| `task changelog` | regenerate `CHANGELOG.md` from git history |

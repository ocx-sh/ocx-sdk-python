# ocx-sdk

[![CI](https://github.com/ocx-sh/ocx-sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/ocx-sh/ocx-sdk-python/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/ocx-sh/ocx-sdk-python/branch/main/graph/badge.svg)](https://codecov.io/gh/ocx-sh/ocx-sdk-python)
[![Docs](https://github.com/ocx-sh/ocx-sdk-python/actions/workflows/docs.yml/badge.svg)](https://ocx-sh.github.io/ocx-sdk-python/)

Python SDK for [OCX](https://ocx.sh).

> **Status: scaffolding only.** The package exposes nothing but `__version__`
> yet. The toolchain, quality gate, docs site, and release pipeline are wired
> and green — the API lands on top.

📖 **Docs: <https://ocx-sh.github.io/ocx-sdk-python/>**

## Install

Requires Python 3.13+. Not on PyPI until the first tagged release; until then
install from git.

```bash
uv add "ocx-sdk~=0.1.0"                                                # after first release
uv add "ocx-sdk @ git+https://github.com/ocx-sh/ocx-sdk-python@main"   # today
```

## Development

This repo dogfoods OCX. Install OCX once, then run everything through it:

```bash
curl -sSL https://setup.ocx.sh | sh
ocx run -- task verify      # format check + lint + types + tests + coverage
ocx run -- task test
ocx run -- task docs:serve  # live-preview the docs site at localhost:8000
```

OCX bootstraps `task`, `uv`, and `git-cliff`; `uv` pulls `ruff` + `pyright` from
`[project.optional-dependencies] dev` and the MkDocs stack from `docs`.

## Stability

Pre-1.0. Breaking changes ship without migration shims — no deprecation
warnings, no compatibility layers. Pin an exact version.

## Coverage

`task verify` enforces ≥80% line + branch coverage on `src/ocx_sdk`. Run
`task cov:html` and open `htmlcov/index.html` to inspect uncovered lines. The
threshold lives in `[tool.coverage.report] fail_under` in `pyproject.toml`.

## License

Apache-2.0 — see `LICENSE`.

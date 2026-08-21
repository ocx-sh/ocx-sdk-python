# ocx-sdk

[![PyPI](https://img.shields.io/pypi/v/ocx-sdk.svg)](https://pypi.org/project/ocx-sdk/)
[![Python versions](https://img.shields.io/pypi/pyversions/ocx-sdk.svg)](https://pypi.org/project/ocx-sdk/)
[![CI](https://github.com/ocx-sh/ocx-sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/ocx-sh/ocx-sdk-python/actions/workflows/ci.yml)
[![Docs](https://github.com/ocx-sh/ocx-sdk-python/actions/workflows/docs.yml/badge.svg)](https://ocx-sh.github.io/ocx-sdk-python/)
[![codecov](https://codecov.io/gh/ocx-sh/ocx-sdk-python/branch/main/graph/badge.svg)](https://codecov.io/gh/ocx-sh/ocx-sdk-python)
[![License](https://img.shields.io/github/license/ocx-sh/ocx-sdk-python.svg)](LICENSE)

**Typed Python handles over [ocx](https://ocx.sh)** — the OCI-registry-backed
binary package manager. Bootstrap ocx anywhere Python runs, drive its
toolchains, author and publish packages against it — all through methods
that mirror ocx's own commands one for one, wrapping the CLI rather than
reimplementing it.

```python-no-run
# illustrative: /srv/build stands in for a real project directory, and
# bootstrap.ensure() needs network access — swap in your own path to run
# this for real, or see the quickstart below for the full explanation.
from ocx_sdk import Ocx, bootstrap

ocx = Ocx(exe=bootstrap.ensure())    # download, verify, cache a pinned binary
project = ocx.project("/srv/build")  # /srv/build holds ocx.toml
project.pull()                       # materialize the declared toolchain
result = project.run(["task", "verify"])
```

📖 **Docs: <https://ocx-sh.github.io/ocx-sdk-python/>** — start at the
[quickstart](https://ocx-sh.github.io/ocx-sdk-python/guide/quickstart/).

## Install

Requires Python 3.12+.

```bash
uv add ocx-sdk
```

```bash
pip install ocx-sdk
```

## Why

- **Zero runtime dependencies.** Stdlib only — nothing else lands in your
  environment.
- **Fully typed.** `py.typed` ships in the wheel; every public method's
  signature and result are pinned, checked, and covered.
- **100% test coverage**, use-case first — coverage is a floor here, not a
  headline.
- **A wrapper, not a reimplementation.** ocx owns identifier resolution and
  checksum verification; the SDK carries identifiers byte-for-byte and
  parses only what ocx already validated.

## Where to go next

- [Quickstart](https://ocx-sh.github.io/ocx-sdk-python/guide/quickstart/) —
  the canonical CI journey in one worked example.
- [Bootstrap](https://ocx-sh.github.io/ocx-sdk-python/guide/bootstrap/) —
  pinning, corporate mirrors, hardened environments.
- [API reference](https://ocx-sh.github.io/ocx-sdk-python/reference/api/) —
  every public symbol.
- [Changelog](https://ocx-sh.github.io/ocx-sdk-python/changelog/).

## Development

This repo dogfoods OCX. Install OCX once, then run everything through it:

```bash
curl -sSL https://setup.ocx.sh | sh
ocx run -- task verify      # format check + lint + types + tests + coverage
ocx run -- task docs:serve  # live-preview the docs site at localhost:8000
```

OCX bootstraps `task`, `uv`, and `git-cliff`; `uv` pulls `ruff` + `pyright`
from `[project.optional-dependencies] dev` and the MkDocs stack from `docs`.
See [Contributing](https://ocx-sh.github.io/ocx-sdk-python/contributing/)
for the full guide, including how the doc examples on this page are tested.

## Stability

Pre-1.0. Breaking changes ship without migration shims — no deprecation
warnings, no compatibility layers. Pin an exact version.

## License

Apache-2.0 — see `LICENSE`.

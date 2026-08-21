# Writing docs

The docs site is [MkDocs](https://www.mkdocs.org/) with
[Material](https://squidfunk.github.io/mkdocs-material/) and
[`mkdocstrings`](https://mkdocstrings.github.io/python/).

## Preview locally

```bash
ocx run -- task docs:serve
```

Browse to <http://127.0.0.1:8000>. Pages live-reload on file changes.

## Strict build (CI parity)

```bash
ocx run -- task docs:build
```

`--strict` flips any broken cross-reference, missing nav entry, or
undefined symbol into a build failure. CI runs the same command.

## Runnable code fences

Every fenced code block in `docs/**/*.md` and `README.md` is collected and
run as a test by the root `conftest.py`'s Sybil hook. Four fence languages
are recognized:

- ` ```python ` — runs unconditionally, unit tier.
- ` ```python-contract ` — needs a live, pinned ocx binary; skipped unless
  `OCX_SDK_CONTRACT=1`.
- ` ```python-acceptance ` — needs the compose stack; skipped unless
  `OCX_SDK_ACCEPTANCE=1`.
- ` ```python-no-run ` — compile-checked only (never executed); use for a
  snippet that references unreachable infrastructure (a corporate mirror, a
  fictional path, a real network call) and say why in a comment.

A fence in any other language (including a bare ` ```python3 ` or a typo) is
silently uncollected rather than failing the build — double-check the
language tag on a new snippet by hand; there is no enforcement that catches
an unrecognized one.

## Adding a page

1. Create the Markdown file under `docs/`.
2. Wire it into `nav:` in `mkdocs.yml`.
3. `task docs:build` to confirm `--strict` is happy.

## Auto-API pages (`mkdocstrings`)

To render a class or function:

```markdown
::: ocx_sdk.module.Symbol
```

`mkdocstrings` reads the live docstring. Update the docstring in
`src/`, rebuild — done.

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

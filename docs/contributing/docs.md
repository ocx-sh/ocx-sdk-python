<!-- doc_type: how-to -->
<!-- doc_tier: everyday -->

# Writing docs

The docs site is [Astro Starlight](https://starlight.astro.build/) with the
shared `@ocx-sh/theme`, built from `site/`. The pages come from `docs/`, and
the API reference comes from the docstrings.

Every page starts with a `doc_type` and `doc_tier` comment that the
`docs-quality` rule reads. Run its checks before you commit:

```bash
python3 .claude/rules/docs-quality/checks/doc_declaration.py --root docs
python3 .claude/rules/docs-quality/checks/page_type.py --root docs
```

## Preview locally

```bash
ocx exec -- task docs:serve
```

Browse to <http://localhost:4321/integrations/python/>. Edits to `docs/` need
a restart of the task, because the pages are generated when it starts.

## Build and check (CI parity)

```bash
ocx exec -- task docs:build
```

The task installs from the lockfile, builds the site, and runs `ocx-site
check` on `site/dist`. A broken internal link, a wrong layout or a Pagefind
version mismatch fails it. CI runs the same task.

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

1. Create the Markdown file under `docs/`, with the two declaration comments
   and one `#` heading. The heading becomes the page title.
2. Add its slug to `sidebar` in `site/astro.config.mjs`.
3. Run `task docs:build` to confirm the build and the link check pass.

Use `:::note[Title]` blocks for callouts, and relative `.md` links between
pages. The generator rewrites those links to the site URLs.

## Auto-API pages

`docs/reference/api.md` holds one `<Autodoc name="ocx_sdk" />` tag. The site
renders the reference from the docstrings with `starlight-pydocs`, but one page
for every symbol is far over the page budgets, so the generator replaces the tag
with a list of group pages (`site/reference-groups.mjs`). To change the
reference, update the docstring in `src/` and rebuild. A new public symbol or
method must also be added to a group there: `task docs:build` fails and names
what is missing, and `task site:lighthouse` names a page that grew over budget.

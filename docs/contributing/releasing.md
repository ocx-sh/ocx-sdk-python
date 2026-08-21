# Releasing

Releases are cut from `main` by tag. `.github/workflows/release.yml` builds,
verifies, and publishes on every push of a `vX.Y.Z` tag.

## One-time setup (owner, before the first tag)

PyPI's [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) needs a
**pending publisher** registered before the first tag push — the workflow
authenticates via OIDC, no stored token:

1. Sign in at [pypi.org](https://pypi.org) → **Your account** → **Publishing**.
2. **Add a new pending publisher** with:
   - PyPI project name: `ocx-sdk`
   - Owner: `ocx-sh`
   - Repository name: `ocx-sdk-python`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

Once the first tagged release publishes successfully, PyPI converts the
pending publisher into a permanent one automatically — no further action.

## Release flow

```bash
ocx run -- task release:prepare
```

Interactive by default (pick `auto | patch | minor | major`), or force a
level with `BUMP=<level>` / pin an exact version with `VERSION=x.y.z`. It
computes the next version from conventional commits via git-cliff, bumps
`pyproject.toml` and the `~=` install snippets, regenerates `CHANGELOG.md`,
and runs `task verify`.

Then, by hand:

```bash
git add -A && git commit -m "chore(release): vX.Y.Z"
git tag vX.Y.Z
git push --atomic origin main vX.Y.Z
```

The tag push triggers `release.yml`: verify → build wheel + sdist via
`uv build` → attach both to a GitHub Release → publish to PyPI.

## Dry-running the pipeline

`release.yml` also runs on manual `workflow_dispatch` — that path builds and
verifies but **never publishes** (the publish job is gated on
`github.ref_type == 'tag'`), so it's safe to trigger from the Actions tab to
sanity-check the pipeline without cutting a real release.

# Contributing

- **[Setup](setup.md)** — bootstrap OCX, `uv`, `task`.
- **[Writing docs](docs.md)** — preview the site, add new pages.

Read the project rules at the top of the repository if you're touching
non-trivial code:

- [`.claude/rules/quality-core.md`](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.claude/rules/quality-core.md) — SOLID / DRY / KISS / YAGNI
- [`.claude/rules/quality-python.md`](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.claude/rules/quality-python.md) — Python 3.13+
- [`.claude/rules/quality-tests.md`](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.claude/rules/quality-tests.md) — pytest standards
- [`.claude/rules/workflow-git.md`](https://github.com/ocx-sh/ocx-sdk-python/blob/main/.claude/rules/workflow-git.md) — branches + conventional commits

Every change lands through `ocx run -- task verify`.

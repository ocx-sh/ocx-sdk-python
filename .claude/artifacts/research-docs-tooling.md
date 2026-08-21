# Research: docs + doc-test tooling

Date: 2026-08-21. Source: researcher worker (source-verified against installed
sybil 10.1.0 + ruff source + empirical uv build). Consumed by: WP-docs,
WP-scaffold.

## mkdocstrings

Current mkdocs.yml baseline correct (google style, members_order source,
merge_init_into_class, spacy sections, signature annotations). Add:
`docstring_options: {ignore_init_summary: true}`, `signature_crossrefs: true`,
`show_if_no_docstring: false`. Keep single `docs/api/index.md` with
`::: ocx_sdk` for the curated ~15-symbol surface; per-module pages only when
real submodule identities emerge. Keep `filters: ["!^_"]`.

## Sybil (v10.1.0, needs py≥3.11, pytest 8+)

**Fence-language markers, NOT suffixes** — Sybil matches fence language by
exact string equality, so the four markers are distinct languages:
` ```python ` (default, always run) · ` ```python-contract ` (skip unless
`OCX_SDK_CONTRACT=1`) · ` ```python-acceptance ` (skip unless
`OCX_SDK_ACCEPTANCE=1`) · ` ```python-no-run ` (ast.parse compile-check only).

conftest.py at repo root (verified API):

```python
import ast, os
import pytest
from sybil import Sybil
from sybil.evaluators.python import PythonEvaluator
from sybil.parsers.markdown import CodeBlockParser, PythonCodeBlockParser

def _gated(env_var):
    run = PythonEvaluator()
    def evaluate(example):
        if os.environ.get(env_var) != "1":
            pytest.skip(f"set {env_var}=1 to run this example")
        run(example)
    return evaluate

def _compile_only(example):
    ast.parse(example.parsed, filename=example.path)

docs = Sybil(
    parsers=[
        PythonCodeBlockParser(),
        CodeBlockParser("python-contract", _gated("OCX_SDK_CONTRACT")),
        CodeBlockParser("python-acceptance", _gated("OCX_SDK_ACCEPTANCE")),
        CodeBlockParser("python-no-run", _compile_only),
    ],
    patterns=["docs/**/*.md"],
    filenames=["README.md"],   # path resolved relative to conftest.py's dir
)
docstrings = Sybil(parsers=[PythonCodeBlockParser()], patterns=["src/**/*.py"])
pytest_collect_file = (docs + docstrings).pytest()
```

- Docstring `>>>` blocks: Sybil's PythonDocStringDocument extracts via ast (no
  import at collection). **Do NOT enable `--doctest-modules`** — would
  double-collect. Add `-p no:doctest` only if doctest flags appear elsewhere.
- `pytest.skip()` inside evaluator works cleanly (SybilItem.runtest calls
  evaluate directly).
- Material/Pygments: `python-contract` etc. may need pymdownx.highlight lang
  mapping for pretty rendering — verify visually in WP-docs.

## ruff D-rules (google convention, verified against ruff source)

```toml
[tool.ruff.lint]
select = [..., "D"]
extend-ignore = ["D105", "D107"]   # magic methods + __init__ (merged into class doc)
[tool.ruff.lint.pydocstyle]
convention = "google"
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["ANN", "D"]
```

`convention="google"` auto-ignores D203 D204 D213 D215 D400 D401 D404
D406-D409 D413; D100-D104 stay active = public-surface docstring enforcement.

## py.typed

Already present in repo (`src/ocx_sdk/py.typed`); hatchling ships it with zero
extra config (empirically verified via uv build + wheel inspection). Add
`Typing :: Typed` classifier in WP-release pyproject enrichment.

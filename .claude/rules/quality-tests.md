---
paths:
  - "tests/**"
  - "**/conftest.py"
  - "**/test_*.py"
---

# Test Quality

Python 3.13 + pytest 8+ standards for `ocx-sdk`. Companion to
`quality-python.md`. Auto-loads when editing anything under `tests/`.

Spec-driven: every test is a small executable specification of one behavior.
The parametrize table *is* the spec. Coverage is a floor, not a goal.

---

## 1. Test identity (FIRST + Right-BICEP + CORRECT)

**FIRST** — every unit test must be:

| Letter | Meaning |
|---|---|
| **F**ast | Milliseconds. No real network, no real disk outside `tmp_path`, no `time.sleep`. |
| **I**ndependent | Any order, any subset. No shared mutable state. |
| **R**epeatable | Same input → same result, every run, every machine. |
| **S**elf-validating | Pass / fail without human inspection. No `print` debug, no eyeballed output. |
| **T**imely | Written alongside (or before) the code under test. |

**Right-BICEP** — what to test for each unit:

| Letter | Test for |
|---|---|
| **B**oundary conditions | empty, one, max, off-by-one, type boundaries |
| **I**nverse relationships | round-trip: `decode(encode(x)) == x` |
| **C**ross-check | independent algorithm or oracle agrees |
| **E**rror conditions | every documented exception path |
| **P**erformance characteristics | only when measurable + relevant |

**CORRECT** — boundary categories (pick those that apply):
**C**onformance, **O**rdering, **R**ange, **R**eference,
**E**xistence, **C**ardinality, **T**ime.

Anti-pattern: high line coverage with weak assertions. Mutation testing
(`mutmut`, `cosmic-ray`) is the canonical answer to "are my assertions
strong" — run on critical modules in a follow-up loop, not every CI push.

---

## 2. Layout & naming

- File: `tests/test_<module>.py` mirrors `src/ocx_sdk/<module>.py`. One
  test module per source module unless coverage explicitly splits.
- Function name: `test_<unit>_<scenario>_<expected>`.
  - Good: `test_fetch_json_raises_when_http_error`, `test_extract_urls_dedups_repeated_urls`.
  - Bad: `test_1`, `test_works`, `test_happy_path`.
- No `TestSomething` classes unless they share a fixture worth grouping. Plain
  functions are the default in this repo.
- Helper functions and fixtures: prefix with `_` to mark them private to the
  test module (`_make_release`, `_handler`).

---

## 3. Structure (AAA, one behavior)

Arrange → Act → Assert, separated by a single blank line:

```python
def test_filter_releases_excludes_prereleases():
    releases = [_release("v1"), _release("v2-rc", prerelease=True)]

    result = filter_releases(releases, include_prereleases=False)

    assert [r.tag_name for r in result] == ["v1"]
```

- **One behavior per test**, not one assertion. Multiple asserts that describe
  the same behavior are fine.
- Negative paths use `pytest.raises(Exc, match="…")`. **Never** bare
  `pytest.raises(Exc)` — assert the message too.

```python
with pytest.raises(ValueError, match="GITHUB_TOKEN is required"):
    github.list_releases("o/r", backend=github.Backend.GRAPHQL)
```

---

## 4. Parametrize as spec

Three or more cases sharing shape → `@pytest.mark.parametrize` with
`pytest.param(..., id="…")`. Explicit ids are **mandatory** — the test report
becomes the spec.

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", [], id="empty-input"),
        pytest.param("https://x/a", ["https://x/a"], id="single-url"),
        pytest.param("(https://x/a)", ["https://x/a"], id="parenthesized"),
    ],
)
def test_extract_urls_table(text: str, expected: list[str]):
    assert extract_urls(text) == expected
```

- One-off edge cases → separate `test_*` function, not parametrize bloat.
- Property-based invariants (`hypothesis`) when you can write a property
  (`decode(encode(x)) == x`); pin regressions with `@example(...)`.

---

## 5. Fixtures

- **Default `scope="function"`.** Widen only with measured cost.
- `tmp_path` for files (not legacy `tmpdir`). `tmp_path_factory` only when
  multiple tests share a read-only tree.
- **`monkeypatch` to set**, `unittest.mock.patch` to assert:
  - env vars → `monkeypatch.setenv("GITHUB_TOKEN", "x")`
  - module attrs → `monkeypatch.setattr(module, "name", value)`
  - call assertions → `unittest.mock.patch` + `assert_called_once_with`
- `conftest.py` only when ≥ 2 files share a fixture. Per-directory scoping:
  pytest searches upward only.
- `autouse=True` only when *every* reachable test needs it (e.g. resetting a
  module-level global). Otherwise an opaque footgun.
- **Factories vs `params=`**:
  - Factory function (`_make_release(...)`) → the *test* shapes the data.
  - `pytest.fixture(params=...)` → the *suite* defines a matrix; pytest emits
    one test per param with its own id.

---

## 6. Mocking

Read these once: Batchelder, [*Why your mock doesn't
work*](https://nedbatchelder.com/blog/201908/why_your_mock_doesnt_work.html);
Fowler, [*Mocks Aren't Stubs*](https://martinfowler.com/articles/mocksArentStubs.html).

- **Patch where used, not where defined.** When `module_under_test` does
  `from x import y`, patch `module_under_test.y`, never `x.y`. The imported
  name in the consuming module is the only one your code sees.
- Prefer `autospec=True` (or `spec=...`) over bare `Mock()` / `MagicMock()` —
  catches signature drift at test time instead of production.
- **Never mock the SUT's own methods.** Mock at the I/O boundary only.
- **`MagicMock` chain depth ≤ 2.** Deeper = the seam is wrong; refactor (DI)
  or write a fake.
- `side_effect` for sequences and exceptions; `return_value` for single replies.
- Don't assert call order unless order is part of the contract.
- `assert_called_once_with(...)` over `assert_called_with(...)` when
  uniqueness matters.

---

## 7. Fakes over mocks (for owned interfaces)

You own the interface? **Write a fake**, not a mock chain.

- `tests/_fakes.py` holds in-memory test doubles that implement the same
  surface as the real class (`FakeFileCache` ↔ `FileCache`).
- Fakes survive interface drift; mocks rot silently and pass when the real
  code is broken.

```python
from tests._fakes import FakeFileCache

def test_list_releases_caches_response():
    cache = FakeFileCache()
    ...
    github.list_releases("o/r", cache=cache)
    assert "o/r/releases" in cache.store
```

Use stdlib `unittest.mock` only for code you don't own (`github3.GitHub`).

---

## 8. HTTP boundary (httpx)

This SDK uses `httpx` (sync). The boundary pattern is **dependency
injection**, not patching.

- Production functions that issue HTTP accept
  `*, client: httpx.Client | None = None`. The default falls back to the
  module's lazy singleton or constructs a per-call client.
- Tests pass a client with a `MockTransport`:

```python
def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"hello": "world"})

def test_fetch_json_returns_parsed_body():
    client = httpx.Client(transport=httpx.MockTransport(_handler))

    body = fetch_json("https://api.example.com/x", client=client)

    assert body == {"hello": "world"}
```

- **Do not** patch `httpx.Client.send`, `httpx.get`, or module-level `_CLIENT`
  globals. If a function isn't injectable, fix the function (refactor commit),
  then write the test.
- `respx` / `pytest-httpx` are not in this project. Add only when
  `MockTransport` handlers grow unmaintainable (multiple files, complex
  routing).

Reference: [httpx Transports / MockTransport](https://www.python-httpx.org/advanced/transports/).

---

## 9. Clocks, env, randomness

- **Inject a clock**, never mock `datetime.now`. Pass `now: Callable[[], datetime]`
  or a `Clock` protocol; default to `datetime.now`.
- Env vars → `monkeypatch.setenv` / `delenv`. Never mutate `os.environ` raw.
- Seed RNGs explicitly (`random.Random(42)`). Never rely on global RNG state.
- Time-dependent logic must be triggerable without sleeping. `time.sleep` in
  a test is a bug.

---

## 10. Forbidden in unit tests

- `time.sleep`, `asyncio.sleep` — use a clock seam.
- Real network — inject the client + `MockTransport`.
- Real disk outside `tmp_path` — never write to `~`, `/tmp`, or cwd directly.
- Shared mutable state between tests (module globals, class attrs, singletons).
- Bare `pytest.raises(Exc)` without `match=`.
- `assert True` / `assert 1 == 1` placeholder asserts.

---

## 11. Coverage workflow

- `task verify` enforces `fail_under` from `pyproject.toml` (branch + line).
- `task cov:html` then open `htmlcov/index.html` to find untested branches.
- Coverage tells you *where* to test; the rule tells you *how*. High coverage
  with weak assertions is the named failure mode.
- Mutation testing is the next gate (`mutmut`, `cosmic-ray`) — documented
  here, wired in a follow-up PR.

---

## Cheat sheet — scan before writing a test

1. Test **behavior**, not implementation.
2. Name: `test_<unit>_<scenario>_<expected>`.
3. AAA with blank-line separation.
4. `pytest.raises(Exc, match="…")` — never bare.
5. `pytest.param(..., id="…")` — always explicit ids.
6. `tmp_path`, never `tmpdir`.
7. `monkeypatch` to **set**, `mock.patch` to **assert**.
8. Patch where the symbol is **used**.
9. `autospec=True` over bare `Mock()`.
10. **Fake** before mock for anything you own.
11. **Inject** `httpx.Client` and clocks; don't patch them.
12. `MagicMock` chain depth ≤ 2.
13. No `time.sleep`, no real network, no real clock.
14. `conftest.py` only when ≥ 2 files need it.
15. The parametrize table *is* the spec.

---

## Decision matrix — pick the right tool

| You are testing… | Use | Avoid |
|---|---|---|
| Pure function | Plain `assert` | `mocker.patch` for no reason |
| Function that issues HTTP | Injected `httpx.Client` + `MockTransport` | Patching `httpx.Client.send` |
| Function that writes to disk | `tmp_path` / `tmp_path_factory` | Hardcoded `/tmp/...`, `tmpdir` |
| Function that reads env / clock | `monkeypatch.setenv`; inject a clock | Mocking `datetime.now`; raw `os.environ` mutation |
| Module-level global state | Function-scoped fixture that resets it | Hidden session-scope leak |
| Wraps a 3rd-party library | Stdlib `mock` (`autospec=True`) at the import seam | Deep `MagicMock` chains |
| Owned collaborator (e.g. `FileCache`) | Hand-written fake in `tests/_fakes.py` | Mock chains that rot with the interface |

---

## Anti-pattern severity (see `quality-core.md`)

| Tier | Test anti-pattern |
|---|---|
| **Block** | Real network in unit test; `time.sleep` in unit test; bare `pytest.raises`; tests assert on private implementation details |
| **Warn** | `MagicMock` chain depth > 2; mocking owned interfaces instead of faking; `autouse` without justification; parametrize without `id="…"` |
| **Suggest** | Helper not prefixed with `_`; missing AAA blank-line separation; one-off case parametrized instead of named test |

---

## Sources

- [pytest docs — Good Integration Practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html)
- [pytest docs — Fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [pytest docs — Parametrize](https://docs.pytest.org/en/stable/example/parametrize.html)
- [pytest docs — monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)
- [`unittest.mock` docs](https://docs.python.org/3/library/unittest.mock.html)
- Batchelder, [*Why your mock doesn't work*](https://nedbatchelder.com/blog/201908/why_your_mock_doesnt_work.html)
- Batchelder, [*Why your mock still doesn't work*](https://nedbatchelder.com/blog/202202/why_your_mock_still_doesnt_work)
- Fowler, [*Mocks Aren't Stubs*](https://martinfowler.com/articles/mocksArentStubs.html)
- Hunt & Thomas, *Pragmatic Unit Testing in Java with JUnit* — FIRST + Right-BICEP
- Langr, Hunt & Thomas, *Pragmatic Unit Testing* — CORRECT boundary categories
- Okken, *Python Testing with pytest* 2nd ed. (Pragmatic, 2022)
- [httpx — Transports / MockTransport](https://www.python-httpx.org/advanced/transports/)
- [Hypothesis docs](https://hypothesis.works/)

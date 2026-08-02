# Install

!!! note "Not on PyPI yet"
    `ocx-sdk` publishes to PyPI from its first tagged release. Until then,
    install from git.

Requires Python 3.13+.

## PEP 723 script

```python
# /// script
# requires-python = ">=3.13"
# dependencies = ["ocx-sdk~=0.1.0"]
# ///
import ocx_sdk

print(ocx_sdk.__version__)
```

```bash
uv run my_script.py
```

## Project dependency

```bash
uv add "ocx-sdk~=0.1.0"
```

## From git (pre-release)

```bash
uv add "ocx-sdk @ git+https://github.com/ocx-sh/ocx-sdk-python@main"
```

Pin a tag instead of `main` once the first release lands — `uv.lock` records
the resolved commit either way.

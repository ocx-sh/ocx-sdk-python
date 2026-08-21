# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Provisioning an ocx binary: `ocx_sdk.bootstrap`.

The one public submodule. Bootstrap is optional — `Ocx()` finds an ocx that
is already on `PATH` or in `$OCX_HOME` — so it lives beside the runtime API
rather than in front of it, and `ensure()` reads as what it is at the call
site:

```python
from ocx_sdk import Ocx, bootstrap

ocx = Ocx(exe=bootstrap.ensure(version="0.5.8"))
```

`ensure` and `DistSource` are also re-exported at the top level for callers
who prefer flat imports; both names refer to the same objects.
"""

from __future__ import annotations

from ._bootstrap import ensure
from ._dist import DistSource

__all__ = [
    "DistSource",
    "ensure",
]

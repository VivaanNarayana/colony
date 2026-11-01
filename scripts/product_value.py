"""Compatibility entry point for the packaged offline product-value scorecard."""

import sys
from pathlib import Path

# Historical source-checkout invocation works before installation, and selects
# this checkout's implementation even if an older package is installed.
_source = Path(__file__).resolve().parents[1] / "src"
if (_source / "colony" / "product_value.py").is_file():
    sys.path.insert(0, str(_source))

from colony.product_value import (  # noqa: E402
    CONTRACT,
    FIELDS,
    MAX_BYTES,
    MODES,
    main,
    native_usage,
    scorecard,
)

__all__ = ["CONTRACT", "FIELDS", "MAX_BYTES", "MODES", "native_usage", "scorecard", "main"]

if __name__ == "__main__":
    main()

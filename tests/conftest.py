"""Isolate test imports/QML singleton startup from the user's real runtime.

Some QML component tests instantiate the registered controller while pumping
Qt events. Isolation must be selected before any LinSub module is imported,
not only patched inside a test body.
"""

import os
import tempfile
from pathlib import Path

_test_root = Path("C:/tmp/ls") if os.name == "nt" and Path("C:/tmp/ls").is_dir() else Path(__file__).resolve().parents[1] / "build" / "t"
_test_root.mkdir(parents=True, exist_ok=True)
_test_runtime = tempfile.TemporaryDirectory(prefix="r-", dir=_test_root, ignore_cleanup_errors=True)
os.environ["LINSUB_SMOKE_TEST"] = "1"  # .env cannot override the test roots.
os.environ["LINSUB_HOME"] = _test_runtime.name
os.environ["RUNTIME_DATA_DIR"] = os.path.join(_test_runtime.name, "data")
os.environ["LINSUB_RESOURCE_ROOT"] = os.path.join(_test_runtime.name, "resources")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

"""Load the repository supervisor without touching the installed runtime."""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import patch

SUPERVISOR = Path(__file__).with_name("proxies-supervisor.py").resolve()


def load_supervisor(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPERVISOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Import normally opens a process-lifetime crash-log fd and loads secrets.
    # The real faulthandler is exercised separately in an isolated subprocess.
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(Path, "home", return_value=Path(directory)),
        patch("os.open", return_value=0),
        patch("faulthandler.enable"),
    ):
        spec.loader.exec_module(module)
    return module


def isolated_import_source(directory: str) -> str:
    """Subprocess source with a test home; no production secrets or log writes."""
    return (
        "import importlib.util; from pathlib import Path;"
        f"Path.home = classmethod(lambda cls: Path({directory!r}));"
        f"spec = importlib.util.spec_from_file_location('s', {str(SUPERVISOR)!r});"
        "m = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(m);"
    )

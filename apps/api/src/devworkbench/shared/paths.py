"""Repository defaults independent of the launch working directory.

DEVWB_HOME and DEVWB_WEB_DIST override paths for installed deployments.
"""
import os
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]

def data_root() -> Path:
    return Path(os.environ.get("DEVWB_HOME", str(REPOSITORY_ROOT / "workspace"))).resolve()

def web_dist() -> Path:
    return Path(os.environ.get("DEVWB_WEB_DIST", str(REPOSITORY_ROOT / "apps" / "web" / "dist"))).resolve()

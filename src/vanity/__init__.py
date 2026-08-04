"""vanity — Home-grown model library management from a local talent agent."""

from .cli import main  # noqa: F401
from .registry import Model, Registry  # noqa: F401
from .util import FetchError, GitFailure, HttpFailure  # noqa: F401

try:  # installed
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("vanity")
except (ImportError, PackageNotFoundError):  # running straight from a clone
    __version__ = "0.2.1"
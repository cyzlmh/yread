"""Local repo-to-wiki generator."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yread")
except PackageNotFoundError:
    __version__ = "0.0.0"

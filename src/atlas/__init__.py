"""Compatibility namespace for the Atlas Python backend.

The backend implementation currently lives in root-level packages such as
``application``, ``domain``, ``infrastructure``, ``presentation``, and
``security``. Public code, docs, the CLI entry point, and tests consistently
refer to those modules through the ``atlas.*`` namespace.

This package keeps that public namespace stable while the repository is being
migrated toward a physical ``src/atlas/...`` backend tree. It aliases the
current root-level implementation packages under ``atlas.*`` without moving the
whole codebase in one high-risk change.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

_ALIAS_PACKAGES = (
    "application",
    "domain",
    "infrastructure",
    "presentation",
    "security",
)

_ALIAS_MODULES = (
    "config",
    "logging_config",
    "mfa",
)


def _alias_root_module(root_name: str) -> ModuleType | None:
    """Expose a root-level module/package as ``atlas.<root_name>``.

    The import is intentionally lazy-tolerant: missing optional modules are
    ignored so partial installs and tooling that only inspect package metadata
    do not fail before the runtime path is fully configured.
    """

    try:
        module = importlib.import_module(root_name)
    except ModuleNotFoundError:
        return None

    atlas_name = f"{__name__}.{root_name}"
    sys.modules[atlas_name] = module
    globals()[root_name] = module
    return module


for _root_name in (*_ALIAS_PACKAGES, *_ALIAS_MODULES):
    _alias_root_module(_root_name)


del _root_name

__all__ = [*_ALIAS_PACKAGES, *_ALIAS_MODULES]

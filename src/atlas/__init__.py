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

    Missing root modules are ignored so package metadata inspection does not
    fail in partial checkouts. Import errors raised from inside an existing
    module are re-raised so real dependency/configuration problems stay visible.
    """

    try:
        module = importlib.import_module(root_name)
    except ModuleNotFoundError as exc:
        if exc.name == root_name:
            return None
        raise

    atlas_name = f"{__name__}.{root_name}"
    sys.modules[atlas_name] = module
    globals()[root_name] = module
    return module


for _root_name in (*_ALIAS_PACKAGES, *_ALIAS_MODULES):
    _alias_root_module(_root_name)


del _root_name

__all__ = [*_ALIAS_PACKAGES, *_ALIAS_MODULES]

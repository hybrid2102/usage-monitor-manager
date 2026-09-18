"""Settings schema, store and presets for each monitor kind.

``schema`` describes the keys, ``store`` reads/validates/writes the file and
``presets`` derives the brand palette. Phase 5 of PLAN.md section 7.
"""

from __future__ import annotations

from . import presets, schema, store

__all__ = ["presets", "schema", "store"]

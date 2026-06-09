from __future__ import annotations

from .patch_apply import PatchApplyResult, apply_unified_diff, restore_backups

__all__ = ["PatchApplyResult", "apply_unified_diff", "restore_backups"]

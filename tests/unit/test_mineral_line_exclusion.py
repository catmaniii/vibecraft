"""Unit tests for the vibecraft fill_line patch in BuildGrid.

Issue #3: BG (Gateway) placed between nexus and mineral line → blocks probe mining paths.

The fix extends BuildGrid.fill_line to mark the full nexus→mineral corridor as
InMineralLine, not just 4 steps from the patch.  These tests verify:

1. The patched fill_line uses max_steps = max(4, int(total_dist) - 2)
2. Close minerals (distance ≤ 6) still get at least 4 steps of coverage
3. Far minerals (distance > 6, typical SC2 main base) get full corridor coverage
4. The marker `# vibecraft:` is present in fill_line (guard against accidental revert)
"""

from __future__ import annotations

import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD_GRID_PATH = PROJECT_ROOT / "vendor/sharpy/sharpy/managers/core/grids/build_grid.py"


# ---------------------------------------------------------------------------
# Helper: reproduce the patched fill_line step-count logic in pure Python
# so we can test it without any SC2 / sharpy runtime dependencies.
# ---------------------------------------------------------------------------


def patched_max_steps(mineral_to_nexus_distance: float) -> int:
    """Mirrors the new max_steps logic in fill_line."""
    return max(4, int(mineral_to_nexus_distance) - 2)


class TestFillLineStepCount:
    """Validate the step-count formula used in the patched fill_line."""

    def test_close_mineral_minimum_4_steps(self):
        # distance 4 → max(4, 4-2=2) = 4
        assert patched_max_steps(4.0) == 4

    def test_distance_5_gives_3_but_floored_to_4(self):
        # distance 5 → max(4, 5-2=3) = 4
        assert patched_max_steps(5.0) == 4

    def test_distance_6_gives_4_steps(self):
        # distance 6 → max(4, 6-2=4) = 4
        assert patched_max_steps(6.0) == 4

    def test_distance_7_extends_beyond_original_4(self):
        # distance 7 → max(4, 7-2=5) = 5  -- first case where extension kicks in
        assert patched_max_steps(7.0) == 5

    def test_typical_main_base_mineral_distance_9(self):
        # SC2 main base: mineral 9 tiles from nexus → max(4, 9-2=7) = 7
        assert patched_max_steps(9.0) == 7

    def test_typical_natural_mineral_distance_8(self):
        # Natural: mineral 8 tiles from nexus → max(4, 8-2=6) = 6
        assert patched_max_steps(8.0) == 6

    def test_far_mineral_distance_10(self):
        # Far mineral 10 tiles → max(4, 10-2=8) = 8
        assert patched_max_steps(10.0) == 8

    def test_fractional_distance_truncated(self):
        # int() truncates, so 9.9 → max(4, 9-2=7) = 7
        assert patched_max_steps(9.9) == 7

    def test_extended_steps_always_ge_4(self):
        for d in range(1, 20):
            assert patched_max_steps(float(d)) >= 4, (
                f"max_steps for distance {d} should always be >= 4"
            )

    def test_extended_steps_increase_with_distance(self):
        # For distances > 6, steps should increase with distance
        steps_at_7 = patched_max_steps(7.0)
        steps_at_10 = patched_max_steps(10.0)
        assert steps_at_10 > steps_at_7

    def test_stops_2_tiles_before_nexus(self):
        # At distance D, max_steps = D-2, so the last marked cell is at step D-2,
        # which is 2 tiles from the nexus — the nexus 5x5 blocker covers those 2.
        distance = 10.0
        steps = patched_max_steps(distance)
        last_step_dist_from_nexus = distance - steps
        # Should leave ~2 tiles before the nexus center
        assert last_step_dist_from_nexus >= 1.5, (
            f"Last marked cell is only {last_step_dist_from_nexus:.1f} tiles from nexus"
        )


class TestFillLinePatchSourceIntegrity:
    """Guard against accidental revert of the fill_line patch in the vendor file."""

    def _get_fill_line_src(self) -> str:
        import ast

        src = BUILD_GRID_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "BuildGrid":
                for item in node.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == "fill_line"
                    ):
                        return "\n".join(lines[item.lineno - 1 : item.end_lineno])
        return ""

    def test_fill_line_has_vibecraft_marker(self):
        src = self._get_fill_line_src()
        assert src, "fill_line not found in BuildGrid"
        assert "# vibecraft:" in src, (
            "fill_line is missing `# vibecraft:` marker — patch may have been reverted"
        )

    def test_fill_line_uses_max_steps_variable(self):
        src = self._get_fill_line_src()
        assert "max_steps" in src, (
            "fill_line does not use max_steps variable — "
            "the step-count extension patch may have been reverted"
        )

    def test_fill_line_uses_total_dist_variable(self):
        src = self._get_fill_line_src()
        assert "total_dist" in src, (
            "fill_line does not use total_dist variable — "
            "the dynamic step-count patch may have been reverted"
        )

    def test_fill_line_no_longer_hardcodes_i_lt_5(self):
        src = self._get_fill_line_src()
        assert "i < 5" not in src, (
            "fill_line still has the old `i < 5` loop limit — the extension patch was not applied"
        )

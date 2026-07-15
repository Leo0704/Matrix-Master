"""matrix.agent.kpi 单元测试。"""
from __future__ import annotations

import pytest

from matrix.agent.kpi import compute_dim_kpi, should_continue


def _m(note_id: str, **kw) -> dict:
    """构造一个 NoteMetric 字典。"""
    base = {
        "note_id": note_id,
        "title": f"n-{note_id}",
        "views": 0,
        "likes": 0,
        "collects": 0,
        "comments": 0,
        "follows_gained": 0,
    }
    base.update(kw)
    return base


class TestComputeDimKpi:
    def test_empty_input(self):
        d = compute_dim_kpi([])
        assert d["exposure"] == {"views": 0, "notes": 0}
        assert d["engagement"]["total"] == 0
        assert d["conversion"]["rate"] == 0.0
        assert d["per_note"] == []

    def test_aggregates_three_dimensions(self):
        d = compute_dim_kpi(
            [
                _m("a", views=100, likes=10, collects=2, comments=1, follows_gained=1),
                _m("b", views=200, likes=20, collects=5, comments=3, follows_gained=2),
            ]
        )
        assert d["exposure"]["views"] == 300
        assert d["exposure"]["notes"] == 2
        # engagement.total = likes(30) + collects(7) + comments(4) + follows(3) = 44
        assert d["engagement"]["likes"] == 30
        assert d["engagement"]["collects"] == 7
        assert d["engagement"]["comments"] == 4
        assert d["engagement"]["follows_gained"] == 3
        assert d["engagement"]["total"] == 44
        # conversion = follows / views = 3 / 300 = 0.01
        assert d["conversion"]["follows_gained"] == 3
        assert d["conversion"]["rate"] == pytest.approx(0.01)
        # rates
        assert d["rates"]["like_rate"] == pytest.approx(30 / 300)
        assert d["rates"]["engage_rate"] == pytest.approx(44 / 300)

    def test_per_note_ratios(self):
        d = compute_dim_kpi([_m("a", views=100, likes=10, comments=5)])
        row = d["per_note"][0]
        assert row["engagement"] == 15  # 10 + 0 + 5 + 0
        assert row["like_rate"] == pytest.approx(0.1)
        assert row["engage_rate"] == pytest.approx(0.15)

    def test_zero_views_yields_zero_rates_not_nan(self):
        d = compute_dim_kpi([_m("a", views=0, likes=5)])
        assert d["conversion"]["rate"] == 0.0
        assert d["rates"]["like_rate"] == 0.0
        assert d["per_note"][0]["like_rate"] == 0.0

    def test_none_fields_treated_as_zero(self):
        d = compute_dim_kpi(
            [{"note_id": "x", "views": None, "likes": None, "follows_gained": None}]
        )
        assert d["exposure"]["views"] == 0
        assert d["engagement"]["total"] == 0

    def test_non_numeric_fields_dont_crash(self):
        d = compute_dim_kpi(
            [{"note_id": "x", "views": "abc", "likes": [], "comments": {}}]
        )
        assert d["exposure"]["views"] == 0
        assert d["engagement"]["likes"] == 0


class TestShouldContinue:
    def test_likes_target_met_returns_stop(self):
        d = compute_dim_kpi([_m("a", views=100, likes=600)])
        ok, reason = should_continue(d, target_likes=500)
        assert ok is False
        assert "likes" in reason
        assert "stop" in reason

    def test_likes_short_and_no_min_views_returns_continue(self):
        # target_likes=500，但 min_views=0, min_engagement=0 → 等价旧逻辑
        d = compute_dim_kpi([_m("a", views=100, likes=10)])
        ok, reason = should_continue(d, target_likes=500)
        assert ok is True
        assert "short" in reason

    def test_views_target_met_stops(self):
        d = compute_dim_kpi([_m("a", views=1000, likes=10)])
        ok, reason = should_continue(d, target_likes=500, min_views=500)
        assert ok is False
        assert "views" in reason
        assert "stop" in reason

    def test_engagement_target_met_stops(self):
        d = compute_dim_kpi(
            [_m("a", views=50, likes=5, collects=3, comments=2, follows_gained=0)]
        )
        ok, reason = should_continue(
            d, target_likes=500, min_views=200, min_engagement=10
        )
        assert ok is False
        assert "engagement" in reason
        assert "stop" in reason

    def test_all_dimensions_short_returns_continue(self):
        d = compute_dim_kpi([_m("a", views=10, likes=5)])
        ok, reason = should_continue(
            d, target_likes=500, min_views=200, min_engagement=50
        )
        assert ok is True
        assert "kpi short" in reason

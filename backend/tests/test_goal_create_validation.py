"""Phase 4 #8：GoalCreate 入参校验测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from matrix.api.schemas.goal import GoalCreate


class TestGoalCreateThemeRequired:
    def test_empty_target_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(type="publish_note", target={})
        assert "theme" in str(exc.value).lower()

    def test_target_without_theme_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(
                type="publish_note",
                target={"audience": "大学生", "product_category": "鞋子"},
            )
        assert "theme" in str(exc.value).lower()

    def test_empty_theme_string_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(type="publish_note", target={"theme": ""})
        assert "theme" in str(exc.value).lower()

    def test_whitespace_only_theme_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(type="publish_note", target={"theme": "   "})
        assert "theme" in str(exc.value).lower()

    def test_non_string_theme_rejected(self):
        with pytest.raises(ValidationError):
            GoalCreate(type="publish_note", target={"theme": 123})

    def test_theme_too_long_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(type="publish_note", target={"theme": "x" * 501})
        assert "500" in str(exc.value)

    def test_theme_exactly_500_accepted(self):
        g = GoalCreate(type="publish_note", target={"theme": "x" * 500})
        assert g.target["theme"] == "x" * 500


class TestGoalCreateHappyPath:
    def test_minimal_valid(self):
        g = GoalCreate(type="publish_note", target={"theme": "平价百搭女鞋带货"})
        assert g.type == "publish_note"
        assert g.target["theme"] == "平价百搭女鞋带货"
        assert g.notes_per_round is None  # 走 DB default

    def test_full_target_with_extras(self):
        g = GoalCreate(
            type="publish_note",
            target={
                "theme": "平价百搭女鞋带货",
                "audience": "大学生",
                "product_category": "鞋子",
                "persona_id": "00000000-0000-0000-0000-000000000001",
                "goal_type": "publish_note",
                "extra": {"price_range": "50-200"},
            },
            target_likes=1000,
            notes_per_round=5,
            max_rounds=4,
        )
        assert g.target_likes == 1000
        assert g.notes_per_round == 5
        assert g.max_rounds == 4
        # extra 字段透传保留
        assert g.target["extra"]["price_range"] == "50-200"

    def test_invalid_target_likes_rejected(self):
        with pytest.raises(ValidationError):
            GoalCreate(
                type="publish_note",
                target={"theme": "x"},
                target_likes=0,  # ge=1
            )

    def test_invalid_notes_per_round_rejected(self):
        with pytest.raises(ValidationError):
            GoalCreate(
                type="publish_note",
                target={"theme": "x"},
                notes_per_round=21,  # le=20
            )

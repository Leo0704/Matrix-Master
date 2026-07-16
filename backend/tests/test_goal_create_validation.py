"""Phase 4 #8：GoalCreate 入参校验测试。"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from matrix.api.schemas.goal import GoalCreate

# v0.7+ 业务模型重构：GoalCreate.business_id 必填；测试用固定 UUID 占位
_TEST_BIZ = uuid.UUID("11111111-1111-1111-1111-111111111111")


class TestGoalCreateThemeRequired:
    def test_empty_target_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(type="publish_note", target={}, business_id=_TEST_BIZ)
        assert "theme" in str(exc.value).lower()

    def test_target_without_theme_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(
                type="publish_note",
                target={"audience": "大学生", "product_category": "鞋子"},
                business_id=_TEST_BIZ,
            )
        assert "theme" in str(exc.value).lower()

    def test_empty_theme_string_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(type="publish_note", target={"theme": ""}, business_id=_TEST_BIZ)
        assert "theme" in str(exc.value).lower()

    def test_whitespace_only_theme_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(type="publish_note", target={"theme": "   "}, business_id=_TEST_BIZ)
        assert "theme" in str(exc.value).lower()

    def test_non_string_theme_rejected(self):
        with pytest.raises(ValidationError):
            GoalCreate(type="publish_note", target={"theme": 123}, business_id=_TEST_BIZ)

    def test_theme_too_long_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoalCreate(type="publish_note", target={"theme": "x" * 501}, business_id=_TEST_BIZ)
        assert "500" in str(exc.value)

    def test_theme_exactly_500_accepted(self):
        g = GoalCreate(type="publish_note", target={"theme": "x" * 500}, business_id=_TEST_BIZ)
        assert g.target["theme"] == "x" * 500


class TestGoalCreateHappyPath:
    def test_minimal_valid(self):
        g = GoalCreate(type="publish_note", target={"theme": "平价百搭女鞋带货"}, business_id=_TEST_BIZ)
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
            business_id=_TEST_BIZ,
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
                business_id=_TEST_BIZ,
                target_likes=0,  # ge=1
            )

    def test_invalid_notes_per_round_rejected(self):
        with pytest.raises(ValidationError):
            GoalCreate(
                type="publish_note",
                target={"theme": "x"},
                business_id=_TEST_BIZ,
                notes_per_round=21,  # le=20
            )

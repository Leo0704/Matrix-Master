"""Pydantic schemas — settings（基于 AppConfig 表 key-value 持久化）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AppSetting(BaseModel):
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    updated_at: str | None = None


class AppSettingList(BaseModel):
    items: list[AppSetting]


class AppSettingUpsert(BaseModel):
    """写一条 setting。"""

    value: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


__all__ = ["AppSetting", "AppSettingList", "AppSettingUpsert"]

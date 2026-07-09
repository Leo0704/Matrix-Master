"""生图客户端（v0.7 Phase 3）。

小红书图文必发。DRAFT 之后必须 IMAGE_GEN 才能进 REVIEW。
provider 选项：
- 通义 Wanxiang（DashScope 原生协议，HTTP 异步轮询 task_id）
- 智谱 CogView（OpenAI 兼容 /v1/images/generations）
- 豆包 Seedream（OpenAI 兼容 /v1/images/generations）

ImageGenResult.urls 是公网可访问图片 URL，APK 端只下载上传，
不感知生图服务内部协议。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ImageGenResult:
    """生图结果。"""

    urls: list[str] = field(default_factory=list)
    revised_prompt: str | None = None
    provider: str = ""
    model: str = ""
    cost_usd: float = 0.0
    seed: int | None = None
    raw: dict[str, Any] | None = None


class ImageGenError(Exception):
    """生图服务调用失败。"""


class ImageGenClient(ABC):
    """生图客户端抽象。"""

    provider: ClassVar[str] = ""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        n: int = 1,
        size: str = "1024*1024",
        style: str | None = None,
        seed: int | None = None,
        negative_prompt: str | None = None,
        timeout: float = 60.0,
    ) -> ImageGenResult: ...


class InMemoryImageGenClient(ImageGenClient):
    """测试 / dev 用：返回占位 URL，不连真实 provider。"""

    provider = "in_memory"

    def __init__(
        self,
        *,
        urls: list[str] | None = None,
        fail: bool = False,
    ) -> None:
        self._urls = urls or ["https://placeholder.invalid/img-0.png"]
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        prompt: str,
        *,
        n: int = 1,
        size: str = "1024*1024",
        style: str | None = None,
        seed: int | None = None,
        negative_prompt: str | None = None,
        timeout: float = 60.0,
    ) -> ImageGenResult:
        self.calls.append(
            {
                "prompt": prompt,
                "n": n,
                "size": size,
                "style": style,
                "seed": seed,
                "negative_prompt": negative_prompt,
            }
        )
        if self._fail:
            raise ImageGenError("simulated image gen failure")
        return ImageGenResult(
            urls=self._urls[:n],
            revised_prompt=prompt,
            provider=self.provider,
            model="in-memory-mock",
        )


class TongyiWanxiangClient(ImageGenClient):
    """通义 Wanxiang（DashScope 原生协议）。"""

    provider = "tongyi_wanxiang"
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self._base_url = base_url or os.environ.get(
            "DASHSCOPE_BASE_URL", self.DEFAULT_BASE_URL
        )

    async def generate(
        self,
        prompt: str,
        *,
        n: int = 1,
        size: str = "1024*1024",
        style: str | None = None,
        seed: int | None = None,
        negative_prompt: str | None = None,
        timeout: float = 60.0,
    ) -> ImageGenResult:
        logger.warning(
            "tongyi_wanxiang.stub api_key_present=%s base_url=%s",
            bool(self._api_key),
            self._base_url,
        )
        raise ImageGenError(
            "TongyiWanxiangClient not wired in v0.7 dev; "
            "use InMemoryImageGenClient for tests"
        )


class ZhipuCogViewClient(ImageGenClient):
    """智谱 CogView（OpenAI 兼容 /v1/images/generations）。"""

    provider = "zhipu_cogview"
    DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
    DEFAULT_MODEL = "cogview-3"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        **kwargs: Any,
    ) -> None:
        self._api_key = api_key or os.environ.get("ZHIPUAI_API_KEY", "")
        self._base_url = base_url or os.environ.get(
            "ZHIPUAI_BASE_URL", self.DEFAULT_BASE_URL
        )
        self._model = model

    async def generate(
        self,
        prompt: str,
        *,
        n: int = 1,
        size: str = "1024*1024",
        style: str | None = None,
        seed: int | None = None,
        negative_prompt: str | None = None,
        timeout: float = 60.0,
    ) -> ImageGenResult:
        logger.warning("zhipu_cogview.stub base_url=%s", self._base_url)
        raise ImageGenError(
            "ZhipuCogViewClient not wired in v0.7 dev; "
            "use InMemoryImageGenClient for tests"
        )


class DoubaoSeedreamClient(ImageGenClient):
    """豆包 Seedream（OpenAI 兼容 /v1/images/generations）。"""

    provider = "doubao_seedream"
    DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
    DEFAULT_MODEL = "doubao-seedream-3-0-t2i-250415"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        **kwargs: Any,
    ) -> None:
        self._api_key = api_key or os.environ.get("DOUBAO_API_KEY", "")
        self._base_url = base_url or os.environ.get(
            "DOUBAO_BASE_URL", self.DEFAULT_BASE_URL
        )
        self._model = model

    async def generate(
        self,
        prompt: str,
        *,
        n: int = 1,
        size: str = "1024*1024",
        style: str | None = None,
        seed: int | None = None,
        negative_prompt: str | None = None,
        timeout: float = 60.0,
    ) -> ImageGenResult:
        logger.warning("doubao_seedream.stub base_url=%s", self._base_url)
        raise ImageGenError(
            "DoubaoSeedreamClient not wired in v0.7 dev; "
            "use InMemoryImageGenClient for tests"
        )


def get_image_gen_client(provider: str | None = None) -> ImageGenClient:
    """按 provider 名取客户端。"""
    p = (provider or os.environ.get("MATRIX_IMAGE_PROVIDER", "in_memory")).lower()
    if p in ("in_memory", "mock", ""):
        return InMemoryImageGenClient()
    if p in ("tongyi", "tongyi_wanxiang", "wanxiang"):
        return TongyiWanxiangClient()
    if p in ("zhipu", "zhipu_cogview", "cogview"):
        return ZhipuCogViewClient()
    if p in ("doubao", "doubao_seedream", "seedream"):
        return DoubaoSeedreamClient()
    raise ValueError(
        f"unknown image provider: {p!r}; "
        "expected in_memory|tongyi_wanxiang|zhipu_cogview|doubao_seedream"
    )


__all__ = [
    "ImageGenClient",
    "ImageGenResult",
    "ImageGenError",
    "InMemoryImageGenClient",
    "TongyiWanxiangClient",
    "ZhipuCogViewClient",
    "DoubaoSeedreamClient",
    "get_image_gen_client",
]

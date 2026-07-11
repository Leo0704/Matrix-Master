"""生图客户端（v0.7 Phase 3）。

小红书图文必发。DRAFT 之后必须 IMAGE_GEN 才能进 REVIEW。
provider 选项：
- 通义 Wanxiang（DashScope 原生协议，HTTP 异步轮询 task_id）
- 智谱 CogView（OpenAI 兼容 /v1/images/generations）
- 豆包 Seedream（OpenAI 兼容 /v1/images/generations）
- MiniMax 文生图（POST /v1/image_generation，Bearer 鉴权）

ImageGenResult.urls 是公网可访问图片 URL，APK 端只下载上传，
不感知生图服务内部协议。

配置源：``matrix.config.Settings``（吃 ``.env`` / 环境变量）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from matrix.config import get_settings
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ImageGenResult:
    """生图结果。"""

    urls: list[str] = field(default_factory=list)
    revised_prompt: str | None = None
    provider: str = ""
    model: str = ""
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
        settings = get_settings()
        self._api_key = api_key or settings.dashscope_api_key or ""
        self._base_url = base_url or settings.dashscope_base_url or self.DEFAULT_BASE_URL

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
        settings = get_settings()
        self._api_key = api_key or settings.zhipuai_api_key or ""
        self._base_url = base_url or settings.zhipuai_base_url or self.DEFAULT_BASE_URL
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
        settings = get_settings()
        self._api_key = api_key or settings.doubao_api_key or ""
        self._base_url = base_url or settings.doubao_base_url or self.DEFAULT_BASE_URL
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


class MiniMaxImageGenClient(ImageGenClient):
    """MiniMax 文生图（POST /v1/image_generation，Bearer 鉴权）。

    支持模型：
    - ``image-01``: 支持 aspect_ratio / width+height
    - ``image-01-live``: 在 image-01 基础上支持 style（style_type/style_weight）

    注：MiniMax 接口无 negative_prompt 字段，传入会被忽略。
    """

    provider = "MiniMax_image_gen"
    DEFAULT_BASE_URL = "https://api.minimaxi.com"
    DEFAULT_MODEL = "image-01"

    _SIZE_TO_ASPECT: ClassVar[dict[str, str]] = {
        "1024*1024": "1:1", "1024x1024": "1:1",
        "1280*720":  "16:9", "1280x720":  "16:9",
        "1152*864":  "4:3",  "1152x864":  "4:3",
        "1248*832":  "3:2",  "1248x832":  "3:2",
        "832*1248":  "2:3",  "832x1248":  "2:3",
        "864*1152":  "3:4",  "864x1152":  "3:4",
        "720*1280":  "9:16", "720x1280":  "9:16",
        "1344*576":  "21:9", "1344x576":  "21:9",
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        **kwargs: Any,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.minimax_api_key or ""
        self._base_url = (base_url or settings.minimax_base_url or self.DEFAULT_BASE_URL).rstrip("/")
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
        if not self._api_key:
            raise ImageGenError("MINIMAX_API_KEY not configured")

        aspect_ratio = self._SIZE_TO_ASPECT.get(size, "1:1")
        body: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "n": n,
            "response_format": "url",
        }
        if seed is not None:
            body["seed"] = seed
        # style 仅 image-01-live 支持（StyleObject 协议）
        if style is not None:
            if self._model == "image-01-live":
                body["style"] = {"style_type": style, "style_weight": 0.8}
            else:
                logger.warning(
                    "MiniMax style only supported for image-01-live; ignoring"
                )
        if negative_prompt is not None:
            logger.warning("MiniMax has no negative_prompt field; ignoring")

        url = f"{self._base_url}/v1/image_generation"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ImageGenError(f"MiniMax image gen network error: {e}") from e

        if resp.status_code >= 400:
            raise ImageGenError(
                f"MiniMax image gen failed: status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        data = resp.json()
        base = data.get("base_resp") or {}
        if base.get("status_code", 0) != 0:
            raise ImageGenError(
                f"MiniMax image gen api error: "
                f"status_code={base.get('status_code')} "
                f"msg={base.get('status_msg')}"
            )
        urls = ((data.get("data") or {}).get("image_urls")) or []
        return ImageGenResult(
            urls=urls,
            revised_prompt=prompt,
            provider=self.provider,
            model=self._model,
            seed=seed,
            raw=data,
        )


def get_image_gen_client(provider: str | None = None) -> ImageGenClient:
    """按 provider 名取客户端。"""
    p = (provider or get_settings().matrix_image_provider or "in_memory").lower()
    if p in ("in_memory", "mock", ""):
        return InMemoryImageGenClient()
    if p in ("tongyi", "tongyi_wanxiang", "wanxiang"):
        return TongyiWanxiangClient()
    if p in ("zhipu", "zhipu_cogview", "cogview"):
        return ZhipuCogViewClient()
    if p in ("doubao", "doubao_seedream", "seedream"):
        return DoubaoSeedreamClient()
    if p in ("minimax", "minimax_image_gen"):
        return MiniMaxImageGenClient()
    raise ValueError(
        f"unknown image provider: {p!r}; "
        "expected in_memory|tongyi_wanxiang|zhipu_cogview|doubao_seedream|MiniMax_image_gen"
    )


__all__ = [
    "ImageGenClient",
    "ImageGenResult",
    "ImageGenError",
    "InMemoryImageGenClient",
    "TongyiWanxiangClient",
    "ZhipuCogViewClient",
    "DoubaoSeedreamClient",
    "MiniMaxImageGenClient",
    "get_image_gen_client",
]

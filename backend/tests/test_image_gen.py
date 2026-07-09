"""v0.7 Phase 3：IMAGE_GEN 节点 + ImageGenClient 单元测试。

- happy path：InMemoryImageGenClient 命中 → draft.images 写入
- fallback=no_image：provider 抛 ImageGenError → draft.images=[] + last_error
- fallback=idle：provider 抛错 → draft=None → REVIEW 必落 ALERT
- KB 缓存命中：跳过 provider，直接用缓存 urls
- 无 image_generator 配置：fallback=no_image 仍放行

测试不写 test_agent.py 是为了避免和别的 agent 冲突。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from matrix.agent._services import AgentServices, reset_services, set_services
from matrix.agent.nodes import image_gen_node
from matrix.agent.nodes.image_gen import _build_image_prompt, _topic_hash
from matrix.agent.protocols import RetrievedChunk
from matrix.llm.image_gen import (
    ImageGenError,
    InMemoryImageGenClient,
)


# ---------------------------------------------------------------------------
# ImageGenClient 客户端单元测试
# ---------------------------------------------------------------------------


class TestInMemoryImageGenClient:
    @pytest.mark.asyncio
    async def test_returns_urls(self):
        urls = ["https://x.com/a.png", "https://x.com/b.png"]
        c = InMemoryImageGenClient(urls=urls)
        result = await c.generate("hello", n=2)
        assert result.urls == urls
        assert result.provider == "in_memory"
        assert len(c.calls) == 1
        assert c.calls[0]["prompt"] == "hello"

    @pytest.mark.asyncio
    async def test_fail_raises_image_gen_error(self):
        c = InMemoryImageGenClient(fail=True)
        with pytest.raises(ImageGenError):
            await c.generate("hello")


# ---------------------------------------------------------------------------
# _topic_hash 工具函数
# ---------------------------------------------------------------------------


class TestTopicHash:
    def test_same_account_same_title_same_hash(self):
        a, b = _topic_hash("acct", "title"), _topic_hash("acct", "title")
        assert a == b

    def test_diff_account_diff_hash(self):
        assert _topic_hash("acct1", "title") != _topic_hash("acct2", "title")

    def test_diff_title_diff_hash(self):
        assert _topic_hash("acct", "title1") != _topic_hash("acct", "title2")

    def test_diff_style_version_diff_hash(self):
        assert _topic_hash("a", "t", 1) != _topic_hash("a", "t", 2)

    def test_returns_hex_string_of_known_length(self):
        h = _topic_hash("a", "t")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# _build_image_prompt
# ---------------------------------------------------------------------------


class TestBuildImagePrompt:
    def test_includes_title(self):
        prompt = _build_image_prompt({"title": "夏日穿搭", "tags": []}, None)
        assert "夏日穿搭" in prompt

    def test_includes_tags_when_present(self):
        prompt = _build_image_prompt({"title": "t", "tags": ["a", "b"]}, None)
        assert "主题风格" in prompt
        assert "a" in prompt and "b" in prompt

    def test_falls_back_to_topic_title(self):
        prompt = _build_image_prompt({"title": ""}, {"title": "话题标题"})
        assert "话题标题" in prompt


# ---------------------------------------------------------------------------
# image_gen_node 节点行为
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    reset_services()
    yield
    reset_services()


def _services_with(
    *,
    image_generator=None,
    kb_retriever=None,
) -> AgentServices:
    """构造测试用 AgentServices。IMAGE_GEN 节点只用 image_generator + kb_retriever。"""

    class _Stub:
        """任意方法都返 mock-friendly 值的占位 stub。"""

    return AgentServices(
        llm=_Stub(),
        kb_retriever=kb_retriever or _NoopKbRetriever(),
        kb_writer=_Stub(),
        device_publisher=_Stub(),
        device_collector=_Stub(),
        notifier=lambda *a, **kw: None,
        image_generator=image_generator,
    )


class _NoopKbRetriever:
    """默认 retrieve 返回空（缓存全 miss）。"""

    async def retrieve(self, query):
        return []


class _CacheHitKbRetriever:
    """retrieve 返回一个含 urls 的 chunk（模拟缓存命中）。"""

    def __init__(self, urls: list[str]) -> None:
        self._urls = urls
        self.calls: list = []

    async def retrieve(self, query):
        self.calls.append(query)
        return [
            RetrievedChunk(
                chunk_id=uuid4(),
                doc_id=uuid4(),
                doc_type="image_asset",
                text=f"cached:{query.query}",
                score=1.0,
                metadata={"urls": list(self._urls)},
            )
        ]


class TestImageGenNode:
    @pytest.mark.asyncio
    async def test_no_draft_title_returns_no_image_fallback(self):
        set_services(_services_with(image_generator=InMemoryImageGenClient()))
        result = await image_gen_node({"draft": {}})
        assert result["draft"]["images"] == []
        assert result["last_error"]["code"] == "IMAGE_GEN_NO_DRAFT_TITLE"

    @pytest.mark.asyncio
    async def test_no_image_generator_no_error_passes_through(self):
        """fallback=no_image 默认：没客户端只是 images=[]，不抛错。"""
        set_services(_services_with(image_generator=None))
        result = await image_gen_node(
            {"draft": {"title": "夏日穿搭", "content": "c", "tags": []}}
        )
        assert result["draft"]["images"] == []
        assert result["last_error"]["code"] == "IMAGE_GEN_NO_CLIENT"

    @pytest.mark.asyncio
    async def test_happy_path_calls_provider_and_writes_urls(self):
        urls = ["https://example.com/a.png"]
        client = InMemoryImageGenClient(urls=urls)
        set_services(_services_with(image_generator=client))
        result = await image_gen_node(
            {"draft": {"title": "夏日穿搭", "content": "c", "tags": ["穿搭"]}}
        )
        assert result["draft"]["images"] == urls
        assert result["last_error"] is None
        assert result["image_cache_hit"] is False
        assert len(client.calls) == 1
        assert "夏日穿搭" in client.calls[0]["prompt"]

    @pytest.mark.asyncio
    async def test_provider_error_fallback_no_image(self):
        """provider 抛错，fallback=no_image：images=[]，last_error 有，但放行进 REVIEW。"""
        client = InMemoryImageGenClient(fail=True)
        set_services(_services_with(image_generator=client))
        result = await image_gen_node(
            {"draft": {"title": "t", "content": "c", "tags": []}}
        )
        assert result["draft"]["images"] == []
        assert result["last_error"]["code"] == "IMAGE_GEN_FAILED"
        # 没强制 ALERT 标记 → REVIEW 还能继续
        assert result["last_error"].get("__force_alert") is None

    @pytest.mark.asyncio
    async def test_provider_error_fallback_idle_drafts_none(self):
        """fallback=idle：provider 抛错 → draft=None 触发 REVIEW 失败 → ALERT。"""
        client = InMemoryImageGenClient(fail=True)
        set_services(_services_with(image_generator=client))
        result = await image_gen_node(
            {
                "draft": {"title": "t", "content": "c", "tags": []},
                "image_gen_fallback": "idle",
            }
        )
        assert result["draft"] is None
        assert result["last_error"]["code"] == "IMAGE_GEN_FAILED"

    @pytest.mark.asyncio
    async def test_kb_cache_hit_skips_provider(self):
        cached_urls = ["https://cache.example.com/img.png"]
        kb = _CacheHitKbRetriever(urls=cached_urls)
        # 即便注入 client，缓存命中也不该调它
        client = InMemoryImageGenClient(urls=["https://fresh.example.com/never.png"])
        set_services(_services_with(image_generator=client, kb_retriever=kb))
        result = await image_gen_node(
            {"draft": {"title": "t", "content": "c", "tags": []}}
        )
        assert result["draft"]["images"] == cached_urls
        assert result["image_cache_hit"] is True
        assert len(client.calls) == 0  # 缓存命中 → 不调 provider
        assert len(kb.calls) == 1  # KB 查过

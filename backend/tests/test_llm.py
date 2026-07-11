"""LLM 客户端层测试。全部使用 mock，不发真实网络请求。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matrix.llm import (
    AnthropicClient,
    AuthError,
    CachedBlock,
    CachedMessages,
    CompletionCache,
    CompletionResult,
    EmbeddingClient,
    InvalidRequestError,
    LLMError,
    LLMTimeoutError,
    OpenAIClient,
    RateLimitError,
    _fix_surrogates,
    get_client,
    reset_client_cache,
    resolve_model,
    retry_with_backoff,
)
from matrix.llm.errors import TimeoutError as ErrorsTimeout
from matrix.llm.prompt_caching import openai_prompt_caching_enabled


# ---------------------------------------------------------------------------
# 辅助：构造 SDK 风格的 mock response
# ---------------------------------------------------------------------------


def _make_anthropic_response(text: str = "Hello", *, input_tokens: int = 10, output_tokens: int = 5):
    block = SimpleNamespace(text=text)
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(
        content=[block],
        usage=usage,
        stop_reason="end_turn",
        model="claude-sonnet-4-5",
    )


def _make_openai_response(text: str = "Hello", *, prompt_tokens: int = 10, completion_tokens: int = 5):
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(
        choices=[choice],
        usage=usage,
        model="gpt-5",
    )


def _make_openai_embedding_response(vectors: list[list[float]]):
    data = [SimpleNamespace(index=i, embedding=v) for i, v in enumerate(vectors)]
    return SimpleNamespace(data=data, model="text-embedding-3-small", usage=SimpleNamespace(prompt_tokens=0, total_tokens=0))


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_llm_error_carries_provider_and_model(self):
        exc = LLMError("boom", provider="openai", model="gpt-5")
        assert exc.provider == "openai"
        assert exc.model == "gpt-5"
        assert "boom" in str(exc)

    def test_specialized_errors_inherit(self):
        assert issubclass(RateLimitError, LLMError)
        assert issubclass(LLMTimeoutError, LLMError)
        assert issubclass(AuthError, LLMError)
        assert issubclass(InvalidRequestError, LLMError)

    def test_timeout_alias(self):
        assert ErrorsTimeout is LLMTimeoutError


# ---------------------------------------------------------------------------
# 模型别名 & 定价
# ---------------------------------------------------------------------------


class TestModelResolution:
    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("sonnet", "claude-sonnet-4-5"),
            ("haiku", "claude-haiku-4-5"),
            ("gpt5", "gpt-5"),
            ("mini", "gpt-5-mini"),
        ],
    )
    def test_alias_resolves(self, alias, expected):
        assert resolve_model(alias) == expected

    def test_unknown_passthrough(self):
        assert resolve_model("custom-model") == "custom-model"

class TestCompletionCache:
    async def test_set_and_get(self):
        cache = CompletionCache()
        result = CompletionResult(
            text="hi", model="m", prompt_tokens=1, completion_tokens=1,
            latency_ms=10, provider="p",
        )
        key = cache.make_key("hello", model="m", max_tokens=10, temperature=0.5)
        assert await cache.get(key) is None
        await cache.set(key, result)
        got = await cache.get(key)
        assert got is result

    async def test_key_includes_all_params(self):
        cache = CompletionCache()
        k1 = cache.make_key("p", model="m", max_tokens=10, temperature=0.5)
        k2 = cache.make_key("p", model="m", max_tokens=20, temperature=0.5)
        k3 = cache.make_key("p", model="m", max_tokens=10, temperature=0.7)
        k4 = cache.make_key("p", model="m", max_tokens=10, temperature=0.5, system="sys")
        assert len({k1, k2, k3, k4}) == 4

    async def test_lru_eviction(self):
        cache = CompletionCache(max_size=2)
        r1 = CompletionResult(text="1", model="m", prompt_tokens=1, completion_tokens=1, latency_ms=1, provider="p")
        r2 = CompletionResult(text="2", model="m", prompt_tokens=1, completion_tokens=1, latency_ms=1, provider="p")
        r3 = CompletionResult(text="3", model="m", prompt_tokens=1, completion_tokens=1, latency_ms=1, provider="p")
        await cache.set("k1", r1)
        await cache.set("k2", r2)
        await cache.set("k3", r3)
        assert await cache.get("k1") is None  # 被淘汰
        assert await cache.get("k2") is r2
        assert await cache.get("k3") is r3

    async def test_ttl_expiry(self):
        cache = CompletionCache(ttl_seconds=0.05)
        r = CompletionResult(text="x", model="m", prompt_tokens=1, completion_tokens=1, latency_ms=1, provider="p")
        await cache.set("k", r)
        assert await cache.get("k") is r
        await asyncio.sleep(0.1)
        assert await cache.get("k") is None

    async def test_clear(self):
        cache = CompletionCache()
        r = CompletionResult(text="x", model="m", prompt_tokens=1, completion_tokens=1, latency_ms=1, provider="p")
        await cache.set("k", r)
        await cache.clear()
        assert await cache.get("k") is None


# ---------------------------------------------------------------------------
# 重试
# ---------------------------------------------------------------------------


class TestRetry:
    async def test_success_first_try(self):
        calls = 0

        @retry_with_backoff(max_attempts=3, backoff=(0.001, 0.001, 0.001))
        async def fn():
            nonlocal calls
            calls += 1
            return "ok"

        assert await fn() == "ok"
        assert calls == 1

    async def test_retry_on_retryable_then_success(self):
        calls = 0

        @retry_with_backoff(max_attempts=3, backoff=(0.001, 0.001, 0.001))
        async def fn():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise LLMError("transient")
            return "ok"

        assert await fn() == "ok"
        assert calls == 3

    async def test_retry_exhausted_raises_last(self):
        calls = 0

        @retry_with_backoff(max_attempts=3, backoff=(0.001, 0.001, 0.001))
        async def fn():
            nonlocal calls
            calls += 1
            raise LLMError(f"fail-{calls}")

        with pytest.raises(LLMError) as ei:
            await fn()
        assert "fail-3" in str(ei.value)
        assert calls == 3

    async def test_non_retryable_raises_immediately(self):
        calls = 0

        @retry_with_backoff(max_attempts=3, backoff=(0.001, 0.001, 0.001), retry_on=(RateLimitError,))
        async def fn():
            nonlocal calls
            calls += 1
            raise AuthError("nope")

        with pytest.raises(AuthError):
            await fn()
        assert calls == 1

    async def test_respects_retry_after_header(self):
        seen_sleeps: list[float] = []

        real_sleep = asyncio.sleep

        async def fake_sleep(d):
            seen_sleeps.append(d)
            await real_sleep(0)

        exc = RateLimitError("rate")
        # 构造一个带 response.headers 的对象
        exc.response = SimpleNamespace(headers={"retry-after": "0.5"})

        with patch("matrix.llm.retry.asyncio.sleep", side_effect=fake_sleep):

            @retry_with_backoff(max_attempts=3, backoff=(0.001, 0.001, 0.001), jitter=0)
            async def fn():
                raise exc

            with pytest.raises(RateLimitError):
                await fn()

        # 第一次退避应该 >= retry-after
        assert any(s >= 0.5 for s in seen_sleeps[:2])


# ---------------------------------------------------------------------------

class TestAnthropicClient:
    async def test_complete_parses_response(self):
        client = AnthropicClient(api_key="test")
        mock_response = _make_anthropic_response("Hello world", input_tokens=20, output_tokens=8)
        client._client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.complete(
            "Hi", model="sonnet", max_tokens=100, temperature=0.7,
        )

        assert result.text == "Hello world"
        assert result.model == "claude-sonnet-4-5"
        assert result.prompt_tokens == 20
        assert result.completion_tokens == 8
        assert result.provider == "anthropic"
        assert result.latency_ms >= 0
        assert result.stop_reason == "end_turn"

        # 验证 SDK 调用参数
        call = client._client.messages.create.await_args
        assert call.kwargs["model"] == "claude-sonnet-4-5"
        assert call.kwargs["max_tokens"] == 100
        assert call.kwargs["messages"] == [{"role": "user", "content": "Hi"}]

    async def test_complete_passes_system(self):
        client = AnthropicClient(api_key="test")
        client._client.messages.create = AsyncMock(return_value=_make_anthropic_response())

        await client.complete("p", model="sonnet", system="be brief")
        call = client._client.messages.create.await_args
        assert call.kwargs["system"] == "be brief"

    async def test_complete_maps_timeout(self):
        client = AnthropicClient(api_key="test")

        # 模拟 anthropic APITimeoutError
        from anthropic import APITimeoutError

        client._client.messages.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))

        with pytest.raises(LLMTimeoutError):
            await client.complete("p", model="sonnet", timeout=0.01)

    async def test_complete_maps_rate_limit(self):
        client = AnthropicClient(api_key="test")
        from anthropic import RateLimitError as AnthropicRL

        err = AnthropicRL(
            message="rate",
            response=MagicMock(headers={"retry-after": "0.5"}),
            body=None,
        )
        client._client.messages.create = AsyncMock(side_effect=err)
        with pytest.raises(RateLimitError):
            await client.complete("p", model="sonnet")

    async def test_complete_maps_auth_error(self):
        client = AnthropicClient(api_key="test")
        from anthropic import AuthenticationError

        err = AuthenticationError(message="bad key", response=MagicMock(), body=None)
        client._client.messages.create = AsyncMock(side_effect=err)
        with pytest.raises(AuthError):
            await client.complete("p", model="sonnet")


# ---------------------------------------------------------------------------
# OpenAIClient
# ---------------------------------------------------------------------------


class TestOpenAIClient:
    async def test_complete_parses_response(self):
        client = OpenAIClient(api_key="test")
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_openai_response("Hello", prompt_tokens=15, completion_tokens=7)
        )

        result = await client.complete("Hi", model="gpt-5", max_tokens=100, temperature=0.5)

        assert result.text == "Hello"
        assert result.model == "gpt-5"
        assert result.prompt_tokens == 15
        assert result.completion_tokens == 7
        assert result.provider == "openai"
        assert result.stop_reason == "stop"

    async def test_complete_with_system(self):
        client = OpenAIClient(api_key="test")
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_openai_response()
        )
        await client.complete("p", model="gpt-5", system="be brief")
        call = client._client.chat.completions.create.await_args
        msgs = call.kwargs["messages"]
        assert msgs[0] == {"role": "system", "content": "be brief"}
        assert msgs[1] == {"role": "user", "content": "p"}

    async def test_complete_maps_bad_request(self):
        client = OpenAIClient(api_key="test")
        from openai import BadRequestError

        err = BadRequestError(message="bad", response=MagicMock(), body=None)
        client._client.chat.completions.create = AsyncMock(side_effect=err)
        with pytest.raises(InvalidRequestError):
            await client.complete("p", model="gpt-5")


# ---------------------------------------------------------------------------
# EmbeddingClient
# ---------------------------------------------------------------------------


class TestEmbeddingClient:
    async def test_embed_returns_vectors(self):
        client = EmbeddingClient(api_key="test")
        vectors = [[0.1] * 1536, [0.2] * 1536]
        client._client.embeddings.create = AsyncMock(
            return_value=_make_openai_embedding_response(vectors)
        )

        result = await client.embed(["hello", "world"])
        assert len(result) == 2
        assert len(result[0]) == 1536
        assert result[0] == vectors[0]
        assert result[1] == vectors[1]

    async def test_embed_empty_returns_empty(self):
        client = EmbeddingClient(api_key="test")
        result = await client.embed([])
        assert result == []

    async def test_embed_unsupported_model(self):
        client = EmbeddingClient(api_key="test")
        with pytest.raises(LLMError):
            await client.embed(["x"], model="gpt-5")

    def test_dimensions_lookup(self):
        from matrix.llm.embeddings import get_embedding_dimensions
        assert get_embedding_dimensions("text-embedding-3-small") == 1536
        assert get_embedding_dimensions("text-embedding-3-large") == 3072
        with pytest.raises(LLMError):
            get_embedding_dimensions("nope")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class TestRouter:
    def setup_method(self):
        reset_client_cache()

    def teardown_method(self):
        reset_client_cache()

    def test_anthropic_alias(self):
        with patch("matrix.llm.router.AnthropicClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = get_client("sonnet")
            mock_cls.assert_called_once()
            assert client is mock_cls.return_value

    def test_openai_model(self):
        with patch("matrix.llm.router.OpenAIClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = get_client("gpt-5")
            mock_cls.assert_called_once()
            assert client is mock_cls.return_value

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError):
            get_client("mystery-model")

    def test_caches_instance(self):
        with patch("matrix.llm.router.AnthropicClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            c1 = get_client("sonnet")
            c2 = get_client("claude-sonnet-4-5")
            assert c1 is c2
            assert mock_cls.call_count == 1

    # --- v0.7 Phase 1：国产 LLM model → OpenAIClient 复用 ---

    def test_deepseek_resolves_to_openai_with_base_url(self):
        with patch("matrix.llm.router.OpenAIClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = get_client("deepseek-chat", api_key="sk-test")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert "deepseek.com" in call_kwargs.get("base_url", "")
            assert call_kwargs.get("api_key") == "sk-test"

    def test_tongyi_alias_resolves_to_qwen_plus(self):
        """'qwen' alias → qwen-plus → tongyi provider → OpenAIClient。"""
        with patch("matrix.llm.router.OpenAIClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = get_client("qwen", api_key="sk-tongyi")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert "dashscope" in call_kwargs.get("base_url", "")

    def test_glm_provider_routing(self):
        with patch("matrix.llm.router.OpenAIClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = get_client("glm-4-flash", api_key="sk-zhipu")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert "bigmodel.cn" in call_kwargs.get("base_url", "")

    def test_doubao_provider_routing(self):
        with patch("matrix.llm.router.OpenAIClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = get_client("doubao-pro-32k", api_key="sk-doubao")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert "volces.com" in call_kwargs.get("base_url", "")

    def test_provider_for_unknown_model_raises(self):
        with pytest.raises(ValueError):
            get_client("totally-fake-model-123")

    def test_model_aliases_resolve_to_full_names(self):
        from matrix.llm.clients import resolve_model

        assert resolve_model("deepseek") == "deepseek-chat"
        assert resolve_model("qwen") == "qwen-plus"
        assert resolve_model("glm") == "glm-4-plus"
        assert resolve_model("doubao") == "doubao-pro-32k"


# ---------------------------------------------------------------------------
# Prompt Caching helpers
# ---------------------------------------------------------------------------


class TestPromptCaching:
    def test_cached_block_to_anthropic(self):
        block = CachedBlock(text="hello", cache_type="ephemeral")
        payload = block.to_anthropic()
        assert payload == {
            "type": "text",
            "text": "hello",
            "cache_control": {"type": "ephemeral"},
        }

    def test_cached_messages_build_with_system(self):
        msgs = CachedMessages(system="persona")
        msgs.add_user("hi", cache=True)
        sys_payload, messages = msgs.build()
        assert sys_payload[0]["cache_control"] == {"type": "ephemeral"}
        assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cached_messages_no_system_cache(self):
        msgs = CachedMessages(system="x", system_cache=False)
        msgs.add_user("hi")
        sys_payload, _ = msgs.build()
        assert sys_payload == "x"

    def test_openai_cache_threshold(self):
        assert openai_prompt_caching_enabled("a" * 1024) is True
        assert openai_prompt_caching_enabled("short") is False


# ---------------------------------------------------------------------------
# 孤儿代理对（emoji 修复）
# ---------------------------------------------------------------------------


class TestFixSurrogates:
    def test_passthrough_normal_string(self):
        assert _fix_surrogates("hello") == "hello"

    def test_passthrough_complete_emoji(self):
        """完整 emoji 字符串（已经是 single codepoint）原样返回。"""
        assert _fix_surrogates("种草 🩵💖") == "种草 🩵💖"

    def test_reassembles_split_surrogate_pair(self):
        """手动构造两个独立码点：修复后应合并成完整 emoji，utf-8 可编码。"""
        # 0xD83E + 0xDD75 → U+1F975 🥵
        broken = "心情" + chr(0xD83E) + chr(0xDD75) + "玛丽珍"
        # 修复前 utf-8 编码会失败
        with pytest.raises(UnicodeEncodeError):
            broken.encode("utf-8")
        # 修复后变成 6 字符 + 完整 codepoint
        fixed = _fix_surrogates(broken)
        assert len(fixed) == 6
        assert ord(fixed[2]) == 0x1F975  # 🥵
        # utf-8 编码 OK
        fixed.encode("utf-8")

    def test_lone_surrogate_kept_as_is(self):
        """单边 orphan（high 或 low）无法配对，原样保留（保守不丢字符）。"""
        s = "心情" + chr(0xD83E)  # 单高代理
        assert _fix_surrogates(s) == s

    def test_empty_and_ascii_unchanged(self):
        assert _fix_surrogates("") == ""
        assert _fix_surrogates("ascii only") == "ascii only"

    def test_mixed_text(self):
        """中英文 + orphan 混合：英文/中文/emoji 都正常，orphan 配对。"""
        broken = "Hello " + chr(0xD83D) + chr(0xDE00) + " world"
        fixed = _fix_surrogates(broken)
        assert "Hello" in fixed
        assert "world" in fixed
        assert "😀" in fixed
        fixed.encode("utf-8")  # 不抛错


# ---------------------------------------------------------------------------
# Anthropic 客户端：emoji prompt + 重试
# ---------------------------------------------------------------------------


class TestAnthropicClientSurrogatesAndRetry:
    async def test_prompt_with_emoji_passes_to_sdk(self):
        """prompt 含真实 emoji 时 SDK 调用参数里 emoji 不应丢失。"""
        client = AnthropicClient(api_key="test")
        client._client.messages.create = AsyncMock(
            return_value=_make_anthropic_response("ok")
        )

        await client.complete("种草 🩵💖", model="sonnet", timeout=5)
        call = client._client.messages.create.await_args
        sent = call.kwargs["messages"][0]["content"]
        # emoji 必须保留下来
        assert "🩵" in sent
        assert "💖" in sent

    async def test_lone_surrogate_prompt_does_not_crash(self):
        """prompt 含孤儿代理对时（json round-trip 出来的）也能正常调 SDK。"""
        client = AnthropicClient(api_key="test")
        client._client.messages.create = AsyncMock(
            return_value=_make_anthropic_response("ok")
        )

        # 模拟 json 走一圈后产生的孤儿代理对
        broken = "种草 ".encode("utf-16", errors="surrogatepass").decode("utf-16")
        await client.complete(broken + "🩵", model="sonnet", timeout=5)
        # 不抛 UnicodeEncodeError = 通过

    async def test_retries_transient_error(self, monkeypatch):
        """LLMError 会被重试，第二次成功。"""
        # 把 retry 的 sleep 短路掉加速测试
        monkeypatch.setattr("matrix.llm.retry.asyncio.sleep", AsyncMock())
        client = AnthropicClient(api_key="test")
        call_count = 0

        async def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise LLMError("transient")
            return _make_anthropic_response("ok")

        client._client.messages.create = AsyncMock(side_effect=side_effect)
        result = await client.complete("p", model="sonnet", timeout=5)
        assert result.text == "ok"
        assert call_count == 2

    async def test_exhausts_retries_then_raises(self, monkeypatch):
        """3 次都失败后抛最后一次的错。"""
        monkeypatch.setattr("matrix.llm.retry.asyncio.sleep", AsyncMock())
        client = AnthropicClient(api_key="test")
        call_count = 0

        async def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise LLMError(f"fail-{call_count}")

        client._client.messages.create = AsyncMock(side_effect=side_effect)
        with pytest.raises(LLMError) as ei:
            await client.complete("p", model="sonnet", timeout=5)
        assert "fail-3" in str(ei.value)
        assert call_count == 3

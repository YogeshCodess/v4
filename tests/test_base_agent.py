"""
Tests for agents/base_agent.py - BaseAgent class with LLM fallback chain.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from agents.base_agent import BaseAgent
from config import settings


class DummyAgent(BaseAgent):
    """Concrete implementation of BaseAgent for testing."""

    async def execute(self, project_context: dict, user_input: str) -> dict:
        """Dummy execute method."""
        return {"status": "success", "output": "dummy result"}

    def get_system_prompt(self, project_context: dict) -> str:
        """Dummy system prompt."""
        return f"You are a helpful assistant for {project_context.get('project_name', 'test')}."


class TestBaseAgentInit:
    """Test BaseAgent initialization."""

    def test_init_with_defaults(self, mock_env_vars):
        """Test initialization with default values."""
        agent = DummyAgent(
            phase_number="1",
            phase_name="Test Phase",
        )
        assert agent.phase_number == "1"
        assert agent.phase_name == "Test Phase"
        # Default model comes from settings.primary_model (env-dependent: glm-4.7 or claude-opus-4-6)
        assert agent.model in ("glm-4.7", "claude-opus-4-6", settings.primary_model)
        # Constructor default — bumped from 8192 to 16384 to fit structured
        # tool-use responses (the netlist + SDD tools produce large payloads).
        assert agent.max_tokens == 16384
        assert agent.tools == []

    def test_init_with_custom_values(self, mock_env_vars):
        """Test initialization with custom values."""
        custom_tools = [{"name": "test_tool"}]
        agent = DummyAgent(
            phase_number="2",
            phase_name="Custom Phase",
            model="claude-haiku-4-5-20251001",
            system_prompt="Custom prompt",
            tools=custom_tools,
            max_tokens=4096,
        )
        assert agent.phase_number == "2"
        assert agent.phase_name == "Custom Phase"
        assert agent.model == "claude-haiku-4-5-20251001"
        assert agent.system_prompt == "Custom prompt"
        assert agent.tools == custom_tools
        assert agent.max_tokens == 4096

    def test_anthropic_client_initialization(self, mock_env_vars):
        """Agent constructor creates an Anthropic client when the key is set.

        Patch only `settings.anthropic_api_key` (not the whole settings
        object) so pydantic-settings fields the constructor reads later —
        deepseek_base_url, glm_base_url, etc. — stay as real strings that
        httpx can parse.
        """
        with patch("agents.base_agent.settings.anthropic_api_key", "sk-ant-test-key"):
            agent = DummyAgent(phase_number="1", phase_name="Test")
            assert agent._anthropic_client is not None
            assert isinstance(agent._anthropic_client, anthropic.Anthropic)


class TestSystemPrompt:
    """Test system prompt generation."""

    def test_get_system_prompt(self, mock_env_vars):
        """Test get_system_prompt returns correct string."""
        agent = DummyAgent(phase_number="1", phase_name="Test")
        context = {"project_name": "MyProject"}
        prompt = agent.get_system_prompt(context)
        assert "MyProject" in prompt
        assert "helpful assistant" in prompt


class TestLog:
    """Test logging functionality."""

    def test_log_info(self, mock_env_vars, caplog):
        """Test info logging."""
        import logging
        caplog.set_level(logging.INFO)
        agent = DummyAgent(phase_number="1", phase_name="TestPhase")
        agent.log("Test message", level="info")
        assert len(caplog.records) > 0
        assert "[1:TestPhase]" in caplog.records[-1].message

    def test_log_warning(self, mock_env_vars, caplog):
        """Test warning logging."""
        import logging
        caplog.set_level(logging.WARNING)
        agent = DummyAgent(phase_number="2", phase_name="WarnPhase")
        agent.log("Warning message", level="warning")
        assert "[2:WarnPhase]" in caplog.records[-1].message


class TestCallLLM:
    """Test LLM calling functionality."""

    def _mock_call_model_response(self, text="Test response content"):
        """Build a standard mock _call_model response."""
        return {
            "content": text,
            "tool_calls": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "model_used": "glm-4.7",
        }

    @pytest.mark.asyncio
    async def test_call_llm_basic(self, mock_env_vars):
        """Test basic LLM call — patch _call_model to avoid network."""
        agent = DummyAgent(phase_number="1", phase_name="Test")
        expected = self._mock_call_model_response()

        with patch.object(agent, "_call_model", return_value=expected):
            result = await agent.call_llm(
                messages=[{"role": "user", "content": "Hello"}]
            )

        assert "content" in result
        assert result["content"] == "Test response content"
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_call_llm_with_system(self, mock_env_vars):
        """Test LLM call passes system prompt to _call_model."""
        agent = DummyAgent(phase_number="1", phase_name="Test")
        captured = {}

        async def fake_call_model(model, messages, system, tools, max_tokens, tool_choice=None):
            captured["system"] = system
            return self._mock_call_model_response()

        with patch.object(agent, "_call_model", side_effect=fake_call_model):
            result = await agent.call_llm(
                messages=[{"role": "user", "content": "Hello"}],
                system="Custom system prompt",
            )

        assert "content" in result
        assert captured["system"] == "Custom system prompt"

    @pytest.mark.asyncio
    async def test_call_llm_with_tools(self, mock_env_vars):
        """Test LLM call passes tools to _call_model."""
        agent = DummyAgent(phase_number="1", phase_name="Test")
        captured = {}

        async def fake_call_model(model, messages, system, tools, max_tokens, tool_choice=None):
            captured["tools"] = tools
            return self._mock_call_model_response()

        custom_tools = [{"name": "test_tool"}]
        with patch.object(agent, "_call_model", side_effect=fake_call_model):
            result = await agent.call_llm(
                messages=[{"role": "user", "content": "Use tool"}],
                tools=custom_tools,
            )

        assert "content" in result
        assert captured["tools"] == custom_tools

    @pytest.mark.asyncio
    async def test_call_llm_with_custom_max_tokens(self, mock_env_vars):
        """Test LLM call passes max_tokens to _call_model."""
        agent = DummyAgent(phase_number="1", phase_name="Test")
        captured = {}

        async def fake_call_model(model, messages, system, tools, max_tokens, tool_choice=None):
            captured["max_tokens"] = max_tokens
            return self._mock_call_model_response()

        with patch.object(agent, "_call_model", side_effect=fake_call_model):
            result = await agent.call_llm(
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=1024,
            )

        assert "content" in result
        assert captured["max_tokens"] == 1024


class TestLLMFallback:
    """Test LLM fallback chain behavior."""

    @pytest.mark.asyncio
    async def test_fallback_on_rate_limit(self, mock_env_vars, monkeypatch):
        """Fallback chain: primary hits rate limit (429) → retried 3x on
        the same model → all 3 fail → falls through to next model → success.

        P26 (2026-04-25) UPDATED SEMANTICS: previously the chain fell
        through to the next model on the FIRST 429. Now we retry the
        SAME model up to 3 times with exponential backoff before giving
        up — this catches transient GLM rate-limits where waiting a few
        seconds clears the throttle, instead of immediately failing
        through to a sibling model on the same account that's also
        rate-limited.
        """
        # Patch the backoff to 0 so the test doesn't actually sleep.
        import agents.base_agent as _ba
        monkeypatch.setattr(_ba, "_RETRY_BACKOFF_BASE_S", 0.0)

        agent = DummyAgent(phase_number="1", phase_name="Test")
        agent.fallback_chain = [agent.model, "claude-haiku-4-5-20251001"]

        success_result = {
            "content": "Fallback response",
            "tool_calls": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }

        mock_response = MagicMock()
        mock_response.status_code = 429
        rate_error = anthropic.RateLimitError(
            message="Rate limited",
            response=mock_response,
            body={"error": {"message": "Rate limit exceeded"}},
        )

        # Primary model: 3x rate_error (exhaust retries) → fall through.
        # Fallback model: success on first try.
        with patch.object(
            agent, "_call_model",
            side_effect=[rate_error, rate_error, rate_error, success_result],
        ):
            result = await agent.call_llm(
                messages=[{"role": "user", "content": "Test"}],
            )

        assert result["content"] == "Fallback response"

    @pytest.mark.asyncio
    async def test_retry_same_model_recovers_from_transient_429(self, mock_env_vars, monkeypatch):
        """P26 (2026-04-25) regression test: a transient 429 that clears
        on the second attempt MUST recover via same-model retry — NOT
        fall through to a (potentially also rate-limited) next model.

        Real failing scenario from project djd:
            DAG fires P3+P6+P8a+P7+P8b+P8c in parallel; HRS internally
            fires 8 sub-section calls in parallel. GLM rate-limiter
            throws a few 429s. Old code fell through to glm-4.7 (same
            Z.AI account, same rate limit) → also 429 → chain exhausted.
            New code: glm-5.1 attempt 1 → 429 → sleep + retry → success.
        """
        import agents.base_agent as _ba
        monkeypatch.setattr(_ba, "_RETRY_BACKOFF_BASE_S", 0.0)

        agent = DummyAgent(phase_number="1", phase_name="Test")
        agent.fallback_chain = [agent.model, "claude-haiku-4-5-20251001"]

        success_result = {
            "content": "Recovered on retry",
            "tool_calls": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
        mock_response = MagicMock()
        mock_response.status_code = 429
        rate_error = anthropic.RateLimitError(
            message="Rate limited", response=mock_response, body={"error": {}},
        )

        # Primary: 1x 429 → retry → success on attempt 2.
        with patch.object(
            agent, "_call_model",
            side_effect=[rate_error, success_result],
        ) as mock_call:
            result = await agent.call_llm(
                messages=[{"role": "user", "content": "Test"}],
            )

        assert result["content"] == "Recovered on retry"
        assert result["model_used"] == agent.model, (
            "should have recovered on the SAME model (primary), not "
            f"fallen through. Got model_used={result.get('model_used')!r}"
        )
        # Verify _call_model was called exactly twice — both with the
        # primary model id.
        assert mock_call.call_count == 2

    @pytest.mark.asyncio
    async def test_permanent_error_does_not_retry(self, mock_env_vars, monkeypatch):
        """P26 (2026-04-25) — permanent errors (auth 401, insufficient
        balance 402, model-not-found 404) must NOT trigger same-model
        retry — those errors don't change on retry. They should fall
        through to the next model immediately so we don't waste time
        sleeping. Real failing scenario: DeepSeek API balance exhausted
        (402). Retrying the SAME DeepSeek call 3 times with backoff
        would just delay the failure by 35 seconds before giving up."""
        import agents.base_agent as _ba
        monkeypatch.setattr(_ba, "_RETRY_BACKOFF_BASE_S", 0.0)

        agent = DummyAgent(phase_number="1", phase_name="Test")
        agent.fallback_chain = [agent.model, "claude-haiku-4-5-20251001"]

        success_result = {
            "content": "ok from fallback",
            "tool_calls": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
        mock_response = MagicMock()
        mock_response.status_code = 402
        auth_error = anthropic.APIStatusError(
            message="Insufficient Balance",
            response=mock_response,
            body={"error": {"message": "Insufficient Balance"}},
        )

        # Primary: ONE 402 → no retry → fall through immediately.
        # Fallback: success.
        with patch.object(
            agent, "_call_model",
            side_effect=[auth_error, success_result],
        ) as mock_call:
            result = await agent.call_llm(
                messages=[{"role": "user", "content": "Test"}],
            )

        assert result["content"] == "ok from fallback"
        assert result["model_used"] != agent.model
        # Exactly 2 calls — 1 to primary (which 402'd), 1 to fallback (success).
        assert mock_call.call_count == 2, (
            f"permanent error should NOT trigger retry on same model — "
            f"expected 2 _call_model invocations, got {mock_call.call_count}"
        )

    @pytest.mark.asyncio
    async def test_all_models_fail_raises_error(self, mock_env_vars):
        """Test that RuntimeError is raised when all models fail."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        # Need to raise non-API errors to trigger fallback chain properly
        # The call_llm catches RateLimitError and token-related APIStatusError
        # Let's mock _call_model to return None (unknown model type)
        with patch.object(agent, "_call_model", return_value=None):
            with pytest.raises(RuntimeError, match="All models in fallback chain failed"):
                await agent.call_llm(messages=[{"role": "user", "content": "Test"}])


class TestCallAnthropic:
    """Test Anthropic-specific API calls."""

    @pytest.mark.asyncio
    async def test_call_anthropic_with_text_response(self, mock_env_vars):
        """Test _call_anthropic returns text content."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Anthropic text")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=5, output_tokens=10)

        agent._anthropic_client = MagicMock()
        agent._anthropic_client.messages.create = MagicMock(return_value=mock_response)

        result = await agent._call_anthropic(
            model="claude-opus-4-6",
            messages=[{"role": "user", "content": "Test"}],
            system="System prompt",
            tools=[],
            max_tokens=1000
        )

        assert result["content"] == "Anthropic text"
        assert result["stop_reason"] == "end_turn"
        assert result["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_call_anthropic_with_tool_use(self, mock_env_vars):
        """Test _call_anthropic parses tool_use blocks."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.id = "tool-123"
        mock_tool_block.name = "search_components"
        mock_tool_block.input = {"query": "resistor"}

        mock_response = MagicMock()
        mock_response.content = [mock_tool_block]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=5, output_tokens=10)

        agent._anthropic_client = MagicMock()
        agent._anthropic_client.messages.create = MagicMock(return_value=mock_response)

        result = await agent._call_anthropic(
            model="claude-opus-4-6",
            messages=[{"role": "user", "content": "Search"}],
            system="System prompt",
            tools=[],
            max_tokens=1000
        )

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "search_components"
        assert result["tool_calls"][0]["id"] == "tool-123"
        assert result["tool_calls"][0]["input"] == {"query": "resistor"}

    @pytest.mark.asyncio
    async def test_call_anthropic_raises_without_client(self, mock_env_vars):
        """Test _call_anthropic raises RuntimeError when client not initialized."""
        agent = DummyAgent(phase_number="1", phase_name="Test")
        agent._anthropic_client = None

        with pytest.raises(RuntimeError, match="Anthropic client not initialized"):
            await agent._call_anthropic(
                model="claude-opus-4-6",
                messages=[{"role": "user", "content": "Test"}],
                system="System",
                tools=[],
                max_tokens=1000
            )


class TestCallOllama:
    """Test Ollama local API calls."""

    @pytest.mark.asyncio
    async def test_call_ollama_success(self, mock_env_vars):
        """Test _call_ollama with successful response."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        # Create mock httpx response - .json() needs to return a coroutine
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = AsyncMock(return_value={
            "message": {"content": "Ollama response"},
            "prompt_eval_count": 5,
            "eval_count": 10,
        })
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client

            result = await agent._call_ollama(
                model="ollama/qwen2.5-coder:32b",
                messages=[{"role": "user", "content": "Test"}],
                system="System prompt",
                max_tokens=1000
            )

        assert result["content"] == "Ollama response"
        assert result["stop_reason"] == "end_turn"
        assert result["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_call_ollama_strips_model_prefix(self, mock_env_vars):
        """Test _call_ollama strips 'ollama/' prefix from model name."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = AsyncMock(return_value={
            "message": {"content": "Response"},
            "prompt_eval_count": 5,
            "eval_count": 10,
        })
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client

            await agent._call_ollama(
                model="ollama/qwen2.5-coder:32b",
                messages=[{"role": "user", "content": "Test"}],
                system="System",
                max_tokens=1000
            )

        # Verify the 'ollama/' prefix was stripped
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:11434/api/chat"
        assert call_args[1]["json"]["model"] == "qwen2.5-coder:32b"


class TestCallGLM:
    """Test GLM-4 / Z.AI API calls."""

    @pytest.mark.asyncio
    async def test_call_glm_anthropic_success(self, mock_env_vars):
        """Test _call_glm_anthropic uses Anthropic SDK with Z.AI base_url."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="GLM response via Z.AI")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=5, output_tokens=10)

        mock_glm_client = MagicMock()
        mock_glm_client.messages.create = MagicMock(return_value=mock_response)

        with patch("agents.base_agent.settings") as mock_settings, \
             patch("agents.base_agent.anthropic.Anthropic", return_value=mock_glm_client):
            mock_settings.glm_api_key = "test-glm-key"
            mock_settings.glm_base_url = "https://api.z.ai/api/anthropic"
            mock_settings.glm_model = "glm-4.7"

            result = await agent._call_glm_anthropic(
                model="glm-4.7",
                messages=[{"role": "user", "content": "Test"}],
                system="System prompt",
                tools=[],
                max_tokens=1000,
            )

        assert result["content"] == "GLM response via Z.AI"
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_call_glm_raises_without_api_key(self, mock_env_vars):
        """Test _call_glm_anthropic raises RuntimeError when API key not set."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        with patch("agents.base_agent.settings") as mock_settings:
            mock_settings.glm_api_key = ""
            mock_settings.glm_base_url = "https://api.z.ai/api/anthropic"
            mock_settings.glm_model = "glm-4.7"

            with pytest.raises(RuntimeError, match="GLM API key not configured"):
                await agent._call_glm_anthropic(
                    model="glm-4.7",
                    messages=[{"role": "user", "content": "Test"}],
                    system="System",
                    tools=[],
                    max_tokens=1000,
                )


class TestCallModel:
    """Test _call_model routing."""

    @pytest.mark.asyncio
    async def test_call_model_routes_to_anthropic(self, mock_env_vars):
        """Test _call_model routes claude* models to _call_anthropic."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Response")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=5, output_tokens=10)

        agent._anthropic_client = MagicMock()
        agent._anthropic_client.messages.create = MagicMock(return_value=mock_response)

        result = await agent._call_model(
            model="claude-opus-4-6",
            messages=[{"role": "user", "content": "Test"}],
            system="System",
            tools=[],
            max_tokens=1000
        )

        assert result is not None
        assert agent._anthropic_client.messages.create.called

    @pytest.mark.asyncio
    async def test_call_model_routes_to_ollama(self, mock_env_vars):
        """Test _call_model routes ollama* models to _call_ollama."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = AsyncMock(return_value={
            "message": {"content": "Ollama response"},
            "prompt_eval_count": 5,
            "eval_count": 10,
        })
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client

            result = await agent._call_model(
                model="ollama/qwen2.5",
                messages=[{"role": "user", "content": "Test"}],
                system="System",
                tools=[],
                max_tokens=1000
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_call_model_routes_to_glm(self, mock_env_vars):
        """Test _call_model routes glm* models to _call_glm_anthropic."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="GLM response")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=5, output_tokens=10)

        mock_glm_client = MagicMock()
        mock_glm_client.messages.create = MagicMock(return_value=mock_response)

        with patch("agents.base_agent.settings") as mock_settings, \
             patch("agents.base_agent.anthropic.Anthropic", return_value=mock_glm_client):
            mock_settings.glm_api_key = "test-key"
            mock_settings.glm_base_url = "https://api.z.ai/api/anthropic"
            mock_settings.glm_model = "glm-4.7"

            result = await agent._call_model(
                model="glm-4.7",
                messages=[{"role": "user", "content": "Test"}],
                system="System",
                tools=[],
                max_tokens=1000,
            )

        assert result is not None
        assert result["content"] == "GLM response"

    @pytest.mark.asyncio
    async def test_call_model_unknown_returns_none(self, mock_env_vars):
        """Test _call_model returns None for unknown model types."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        result = await agent._call_model(
            model="unknown-model",
            messages=[{"role": "user", "content": "Test"}],
            system="System",
            tools=[],
            max_tokens=1000
        )

        assert result is None


class TestCallLLMWithTools:
    """Test call_llm_with_tools method."""

    @pytest.mark.asyncio
    async def test_call_llm_with_tools_no_tool_calls(self, mock_env_vars):
        """Test call_llm_with_tools when model doesn't use tools."""
        agent = DummyAgent(phase_number="1", phase_name="Test")
        mock_result = {"content": "Test response content", "tool_calls": [], "stop_reason": "end_turn", "usage": {}}

        with patch.object(agent, "_call_model", return_value=mock_result):
            result = await agent.call_llm_with_tools(
                messages=[{"role": "user", "content": "Hello"}],
                tool_handlers={},
            )

        assert result["content"] == "Test response content"

    @pytest.mark.asyncio
    async def test_call_llm_with_tools_with_execution(self, mock_env_vars):
        """Test call_llm_with_tools executes tool handlers."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        tool_response = {
            "content": "",
            "tool_calls": [{"id": "tool-123", "name": "test_tool", "input": {"arg": "value"}}],
            "stop_reason": "end_turn",
            "usage": {},
        }
        final_response = {
            "content": "Final answer",
            "tool_calls": [],
            "stop_reason": "end_turn",
            "usage": {},
        }

        async def test_handler(input_data):
            return {"result": f"processed {input_data['arg']}"}

        with patch.object(agent, "_call_model", side_effect=[tool_response, final_response]):
            result = await agent.call_llm_with_tools(
                messages=[{"role": "user", "content": "Use tool"}],
                tool_handlers={"test_tool": test_handler},
            )

        assert "Final answer" in result["content"]

    @pytest.mark.asyncio
    async def test_call_llm_with_tools_handler_error(self, mock_env_vars):
        """Test call_llm_with_tools handles tool handler errors gracefully."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        tool_response = {
            "content": "",
            "tool_calls": [{"id": "tool-123", "name": "failing_tool", "input": {}}],
            "stop_reason": "end_turn",
            "usage": {},
        }
        final_response = {
            "content": "Got error",
            "tool_calls": [],
            "stop_reason": "end_turn",
            "usage": {},
        }

        async def failing_handler(input_data):
            raise ValueError("Tool failed")

        with patch.object(agent, "_call_model", side_effect=[tool_response, final_response]):
            result = await agent.call_llm_with_tools(
                messages=[{"role": "user", "content": "Use tool"}],
                tool_handlers={"failing_tool": failing_handler},
            )

        assert "Got error" in result["content"]

    @pytest.mark.asyncio
    async def test_call_llm_with_tools_max_iterations(self, mock_env_vars):
        """Test call_llm_with_tools respects max_iterations limit."""
        agent = DummyAgent(phase_number="1", phase_name="Test")

        looping_response = {
            "content": "",
            "tool_calls": [{"id": "tool-123", "name": "loop_tool", "input": {}}],
            "stop_reason": "end_turn",
            "usage": {},
        }

        async def loop_handler(input_data):
            return {"keep_looping": True}

        with patch.object(agent, "_call_model", return_value=looping_response):
            result = await agent.call_llm_with_tools(
                messages=[{"role": "user", "content": "Loop"}],
                tool_handlers={"loop_tool": loop_handler},
                max_iterations=2,
            )

        # Should return after max_iterations with empty content
        assert result["content"] == ""

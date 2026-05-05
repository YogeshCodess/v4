#!/bin/bash
# Silicon to Software (S2S) — Production startup (FastAPI only, no --reload, no Streamlit)
# Used by: Docker container (docker-compose.yml)
# For local dev, use start.sh instead.

set -e

echo "================================================"
echo "  ⚡ Silicon to Software (S2S) — AI Hackathon 2026"
echo "  Production Mode"
echo "================================================"

# Validate at least one LLM key is present
if [ -z "$ANTHROPIC_API_KEY" ] && [ -z "$GLM_API_KEY" ] && [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "⚠️  WARNING: No LLM API key found."
    echo "   Set ANTHROPIC_API_KEY, GLM_API_KEY, or DEEPSEEK_API_KEY."
    echo "   The server will start but AI calls will fail."
fi

# Print active LLM
if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "✅ LLM: Claude (Anthropic)"
elif [ -n "$DEEPSEEK_API_KEY" ]; then
    echo "✅ LLM: DeepSeek-V3"
elif [ -n "$GLM_API_KEY" ]; then
    echo "✅ LLM: GLM-4 (Z.AI)"
else
    echo "⚠️  LLM: Air-gapped / Ollama fallback only"
fi

echo ""
echo "Starting FastAPI on http://0.0.0.0:8000 ..."
echo "  React UI  : http://localhost:8000/app"
echo "  API docs  : http://localhost:8000/docs"
echo "  Health    : http://localhost:8000/health"
echo "================================================"

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 2 \
    --log-level info \
    --access-log

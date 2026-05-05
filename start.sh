#!/bin/bash
# Silicon to Software (S2S) — startup script
# Runs FastAPI backend + Streamlit UI side by side

set -e

echo "================================================"
echo "  ⚡ Silicon to Software (S2S) — AI Hackathon 2026"
echo "================================================"

# Check for API keys
if [ -z "$ANTHROPIC_API_KEY" ] && [ -z "$GLM_API_KEY" ]; then
    echo "⚠️  WARNING: No LLM API key found (ANTHROPIC_API_KEY or GLM_API_KEY)"
    echo "   The app will start but AI calls will fail."
    echo "   Set your key in .env and restart."
fi

if [ -n "$GLM_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "✅ Using GLM-4.7 via Z.AI as primary LLM"
fi

if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "✅ Using Claude (Anthropic) as primary LLM"
fi

echo ""
echo "Starting FastAPI backend on http://localhost:8000 ..."
uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --log-level info &

FASTAPI_PID=$!

# Wait for FastAPI to be ready
echo "Waiting for backend to start..."
for i in $(seq 1 20); do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Backend ready!"
        break
    fi
    sleep 1
done

echo ""
echo "Starting Streamlit UI on http://localhost:8501 ..."
streamlit run app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false &

STREAMLIT_PID=$!

echo ""
echo "================================================"
echo "  🚀 Silicon to Software (S2S) is LIVE"
echo "  Backend : http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"
echo "  UI      : http://localhost:8501"
echo "================================================"
echo ""

# Wait for either process to exit
wait $FASTAPI_PID $STREAMLIT_PID

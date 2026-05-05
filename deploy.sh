#!/bin/bash
# Silicon to Software (S2S) - Deployment Script
# Usage: ./deploy.sh [dev|prod]

set -e

ENV=${1:-dev}
PORT=${2:-8501}

echo "=========================================="
echo "Silicon to Software (S2S) Deployment Script"
echo "=========================================="
echo "Environment: $ENV"
echo "Port: $PORT"
echo ""

# Check Python version
echo "Checking Python version..."
python --version || { echo "ERROR: Python not found"; exit 1; }

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -q -r requirements.txt || { echo "ERROR: Failed to install dependencies"; exit 1; }

# Run tests
echo ""
echo "Running tests..."
python -m pytest tests/ -q --tb=no || { echo "WARNING: Some tests failed"; }

# Check if .env exists
if [ ! -f .env ]; then
    echo ""
    echo "WARNING: .env file not found"
    echo "Creating from template..."
    cat > .env << 'ENVEOF'
# Silicon to Software (S2S) Environment Variables

# API Keys (set at least one)
ANTHROPIC_API_KEY=

# Optional API Keys
# OPENAI_API_KEY=
# GLM_API_KEY=
# OLLAMA_BASE_URL=http://localhost:11434

# Database
DATABASE_URL=sqlite:///hardware_pipeline.db

# Mode
MODE=online
ENVEOF
    echo "✅ Created .env file"
    echo "⚠️  Please edit .env and add your API keys"
fi

# Create necessary directories
echo ""
echo "Creating directories..."
mkdir -p outputs
mkdir -p outputs/projects
mkdir -p outputs/documents
mkdir -p logs
echo "✅ Directories created"

# Start Streamlit
echo ""
echo "=========================================="
echo "Starting Silicon to Software (S2S)..."
echo "=========================================="
echo "UI will be available at: http://localhost:$PORT"
echo ""

if [ "$ENV" = "prod" ]; then
    streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
else
    streamlit run app.py --server.port $PORT
fi

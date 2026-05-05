# Silicon to Software (S2S) - Quick Start Guide

## Prerequisites

- Python 3.14 or higher
- pip (Python package manager)
- At least one API key (Anthropic Claude recommended)

## Installation

### 1. Clone or Navigate to Project

```bash
cd c:\Users\HP\OneDrive\Desktop\AI\S2S\S2S_V2
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your API key
# ANTHROPIC_API_KEY=your_key_here
```

### 4. Run Tests (Optional)

```bash
# Run all tests
python -m pytest tests/ -v

# Run only core tests
python -m pytest tests/test_config.py tests/test_agents.py -v
```

## Starting the Application

### Option A: Using Deployment Script (Recommended)

**Windows:**
```bash
deploy.bat dev
```

**Linux/Mac:**
```bash
chmod +x deploy.sh
./deploy.sh dev
```

### Option B: Direct Streamlit Command

```bash
streamlit run app.py
```

### Option C: Custom Port

```bash
streamlit run app.py --server.port 8080
```

## Accessing the UI

Once started, open your browser to:
- **Local:** http://localhost:8501
- **Network:** http://YOUR_IP:8501

## First Time Use

1. **Create a Project**
   - Click "➕ New Project"
   - Enter project name and description
   - Select design type
   - Click "🚀 Create & Start"

2. **Describe Your Design**
   - Go to "💬 Design Chat"
   - Describe your hardware design
   - Wait for AI to generate draft

3. **Approve & Generate**
   - Click "✅ Approve" on the draft
   - Watch the pipeline execute
   - View generated documents

## Production Deployment

### Quick Production Start

```bash
# Windows
deploy.bat prod

# Linux/Mac
./deploy.sh prod
```

### With Custom Port

```bash
deploy.bat prod 8080
```

### Manual Production Start

```bash
streamlit run app.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes* | - | Claude API key |
| `OPENAI_API_KEY` | No | - | OpenAI API key (fallback) |
| `GLM_API_KEY` | No | - | GLM API key (fallback) |
| `OLLAMA_BASE_URL` | No | - | Ollama URL for local LLM |
| `DATABASE_URL` | No | sqlite:///hardware_pipeline.db | Database connection |
| `MODE` | No | online | "online" or "air_gapped" |

*At least one API key is required

## Troubleshooting

### Port Already in Use

```bash
# Find process using port 8501
netstat -ano | findstr :8501

# Kill the process (Windows)
taskkill /PID <PID> /F

# Or use a different port
streamlit run app.py --server.port 8502
```

### API Key Not Working

```bash
# Check .env file exists
ls -la .env

# Verify API key format (no spaces, correct key)
cat .env
```

### Import Errors

```bash
# Reinstall dependencies
pip install --force-reinstall -r requirements.txt
```

### UI Not Loading

```bash
# Check Streamlit server
curl http://localhost:8501

# Check browser console for errors
# Try hard refresh: Ctrl+Shift+R
```

## Directory Structure

```
S2S_V2/
├── app.py                 # Main Streamlit application
├── config.py              # Configuration management
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables (create this)
├── static/
│   └── style.css          # Custom dark theme styles
├── agents/                # AI agents for each pipeline phase
├── database/              # Database models and initialization
├── services/              # Business logic services
├── tests/                 # Test suite
├── outputs/               # Generated outputs (auto-created)
└── docs/                  # Documentation
```

## Pipeline Phases

| Phase | Agent | Output |
|-------|-------|--------|
| P1 | Requirements Agent | User requirements |
| P2 | Document Agent | Initial documentation |
| P3 | Compliance Agent | Compliance checks |
| P4 | Netlist Agent | Netlist visualization |
| P5 | (Manual Review) | - |
| P6 | GLR Agent | Graph-based representation |
| P7 | (Manual Review) | - |
| P8a | SRS Agent | Software requirements |
| P8b | SDD Agent | Software design |
| P8c | Code Agent | Generated code & tests |

## Support

- **Documentation:** See `docs/` folder
- **Test Results:** See `TEST_COMPLETION_REPORT.md`
- **Bug Log:** See `BUG_LOG.md`
- **Production Guide:** See `PRODUCTION_READINESS.md`

## Next Steps

1. ✅ Install dependencies
2. ✅ Configure API keys
3. ✅ Start the application
4. ✅ Create your first project
5. ✅ Generate hardware documentation

**Happy Hardware Designing! 🚀**

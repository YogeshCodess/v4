# API Key Configuration Guide

This guide explains how to configure and test API keys for the Silicon to Software (S2S) AI System.

## Required API Keys

### 1. Anthropic API Key (Primary - Required)

The **Anthropic API key** is required for the system to function. This is used for:
- Claude Opus 4.6 (primary model for complex reasoning)
- Claude Haiku 4.5 (fast model for simple tasks)

#### Getting an Anthropic API Key

1. Go to https://console.anthropic.com/
2. Sign up or log in
3. Navigate to API Keys
4. Create a new API key
5. Copy the key (starts with `sk-ant-...`)

#### Configuration

Add to your `.env` file:

```bash
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Or set as environment variable:

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

#### Test Your Key

```python
from config import settings
print(f"API Key configured: {bool(settings.anthropic_api_key)}")
print(f"Primary Model: {settings.primary_model}")
print(f"Fast Model: {settings.fast_model}")
```

---

## Optional API Keys

### 2. OpenAI API Key (Optional - for Embeddings)

Used for:
- Text embeddings for component search
- ChromaDB vector storage

#### Getting an OpenAI API Key

1. Go to https://platform.openai.com/api-keys
2. Sign up or log in
3. Create a new API key
4. Copy the key (starts with `sk-...`)

#### Configuration

```bash
OPENAI_API_KEY=sk-your-openai-key-here
```

### 3. Zhipu AI GLM API Key (Optional - Fallback)

Used as a fallback when:
- Claude API is unavailable
- Rate limits are reached
- Air-gapped mode (if configured)

#### Getting a GLM API Key

1. Go to https://open.bigmodel.cn/
2. Sign up or log in
3. Navigate to API Keys
4. Create a new API key

#### Configuration

```bash
GLM_API_KEY=your-glm-api-key-here
```

---

## Environment Setup

### Option 1: Using `.env` File (Recommended)

Create a `.env` file in the project root:

```bash
# .env file

# Required: Anthropic API Key
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Optional: OpenAI API Key (for embeddings)
OPENAI_API_KEY=sk-your-openai-key-here

# Optional: GLM API Key (fallback)
GLM_API_KEY=your-glm-api-key-here

# Optional: ChromaDB settings
CHROMA_PERSIST_DIR=./data/chroma
CHROMA_COLLECTION_NAME=components

# Optional: Ollama settings (for air-gapped mode)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:32b
```

### Option 2: Environment Variables

Set environment variables in your shell:

```bash
# Linux/Mac
export ANTHROPIC_API_KEY=sk-ant-your-key-here
export OPENAI_API_KEY=sk-your-openai-key-here
export GLM_API_KEY=your-glm-api-key-here

# Windows CMD
set ANTHROPIC_API_KEY=sk-ant-your-key-here
set OPENAI_API_KEY=sk-your-openai-key-here
set GLM_API_KEY=your-glm-api-key-here

# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-your-key-here"
$env:OPENAI_API_KEY="sk-your-openai-key-here"
$env:GLM_API_KEY="your-glm-api-key-here"
```

---

## Testing API Keys

### Quick Test Script

Create a file `test_api_keys.py`:

```python
import asyncio
from config import settings

async def test_api_keys():
    print("Testing API Key Configuration\n")
    print("=" * 50)

    # Test Anthropic
    if settings.anthropic_api_key:
        print("✅ Anthropic API Key: CONFIGURED")
        print(f"   Key: {settings.anthropic_api_key[:10]}...{settings.anthropic_api_key[-4:]}")
        print(f"   Primary Model: {settings.primary_model}")
        print(f"   Fast Model: {settings.fast_model}")

        # Test API call
        from agents.base_agent import BaseAgent

        class TestAgent(BaseAgent):
            def __init__(self):
                super().__init__(phase_number="TEST", phase_name="Test")
            def get_system_prompt(self, ctx):
                return "Test"
            async def execute(self, ctx, inp):
                return {}

        agent = TestAgent()
        response = await agent.call_llm(
            messages=[{"role": "user", "content": "Say 'API works!'"}]
        )

        if "API works!" in response.get("content", ""):
            print("✅ Anthropic API: CONNECTION SUCCESSFUL")
            print(f"   Response: {response.get('content', '')[:50]}")
        else:
            print("❌ Anthropic API: UNEXPECTED RESPONSE")
    else:
        print("❌ Anthropic API Key: NOT CONFIGURED")

    # Test OpenAI
    print("\n" + "-" * 50)
    if settings.openai_api_key:
        print("✅ OpenAI API Key: CONFIGURED")
        print(f"   Key: {settings.openai_api_key[:10]}...{settings.openai_api_key[-4:]}")
    else:
        print("⚠️  OpenAI API Key: NOT CONFIGURED (optional)")

    # Test GLM
    print("-" * 50)
    if settings.glm_api_key:
        print("✅ GLM API Key: CONFIGURED")
        print(f"   Key: {settings.glm_api_key[:10]}...{settings.glm_api_key[-4:]}")
    else:
        print("⚠️  GLM API Key: NOT CONFIGURED (optional)")

    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(test_api_keys())
```

Run it:
```bash
python test_api_keys.py
```

---

## Testing with Full Architecture

Run the comprehensive test suite:

```bash
python test_full_architecture.py
```

This will test:
- ✅ API key configuration
- ✅ All agent imports
- ✅ Agent initialization
- ✅ Generator functionality
- ✅ CodeReviewer
- ✅ LLM connection
- ✅ ComponentSearchTool
- ✅ Mini end-to-end pipeline

---

## Common Issues

### Issue 1: "Anthropic API key not found"

**Solution:**
```bash
# Check if .env file exists
ls -la .env

# Check if API key is set
echo $ANTHROPIC_API_KEY

# Verify .env is in project root
pwd  # Should be in /path/to/S2S_V2
```

### Issue 2: "Rate limit exceeded"

**Solution:**
- Wait a few minutes before retrying
- Use the fast model (Haiku) for less critical tasks
- Configure fallback chain in config.py

### Issue 3: "ImportError: No module named 'anthropic'"

**Solution:**
```bash
pip install anthropic
```

### Issue 4: ChromaDB compatibility issues

**Solution:**
- ChromaDB is optional
- System will use LLM fallback automatically
- See KNOWN_ISSUES_RESOLVED.md for details

---

## Production Configuration

For production deployment, use environment variables or a secure secrets manager:

### Using Docker Secrets

```yaml
# docker-compose.yml
version: '3.8'
services:
  app:
    image: hardware-pipeline:latest
    environment:
      - ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_key
    secrets:
      - anthropic_key

secrets:
  anthropic_key:
    file: ./secrets/anthropic_key.txt
```

### Using Kubernetes Secrets

```bash
kubectl create secret generic api-keys \
  --from-literal=anthropic=sk-ant-your-key-here
```

---

## Security Best Practices

1. **Never commit API keys to git**
   ```bash
   # Add to .gitignore
   echo ".env" >> .gitignore
   echo "secrets/" >> .gitignore
   ```

2. **Use different keys for dev/staging/prod**
   ```bash
   # .env.development
   ANTHROPIC_API_KEY=sk-ant-dev-key

   # .env.production
   ANTHROPIC_API_KEY=sk-ant-prod-key
   ```

3. **Rotate keys regularly**
   - Set calendar reminders for key rotation
   - Monitor usage for unusual activity

4. **Set rate limits**
   - Configure usage limits in Anthropic console
   - Implement application-level rate limiting

---

## Next Steps

After configuring API keys:

1. **Test the connection:**
   ```bash
   python test_api_keys.py
   ```

2. **Run full architecture test:**
   ```bash
   python test_full_architecture.py
   ```

3. **Run a demo:**
   ```bash
   python run_interactive.py
   ```

4. **Start the UI:**
   ```bash
   streamlit run app.py
   ```

---

**Need Help?**
- Check [config.py](config.py) for configuration options
- See [KNOWN_ISSUES_RESOLVED.md](KNOWN_ISSUES_RESOLVED.md) for common issues
- Review [test_full_architecture.py](test_full_architecture.py) for examples

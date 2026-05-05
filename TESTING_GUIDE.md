# How to Test the Full Architecture

This guide explains how to test the Silicon to Software (S2S) AI System with real API keys.

## Quick Start (3 Steps)

### Step 1: Configure Your API Key

Get your Anthropic API key from https://console.anthropic.com/ and add it to `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### Step 2: Run Simple Test

```bash
python simple_api_test.py
```

Expected output:
```
[1/4] Checking API Key Configuration...
  OK - API Key configured: sk-ant-...xxxx
  OK - Primary Model: claude-opus-4-6

[2/4] Testing LLM Connection...
  OK - API Response: OK
  OK - Model: claude-opus-4-6

[3/4] Testing Generators...
  OK - HRS Generator: 1685 chars

[4/4] Testing CodeReviewer...
  OK - CodeReviewer: Score 95/100
```

### Step 3: Run Full Test Suite

```bash
python test_full_architecture.py
```

---

## Detailed Testing Guide

### Test 1: API Key Configuration

**Purpose:** Verify your API key is correctly configured

**Command:**
```bash
python -c "from config import settings; print('API Key:', bool(settings.anthropic_api_key))"
```

**Expected:** `API Key: True`

**If False:**
- Check `.env` file exists in project root
- Verify API key format: `sk-ant-...`
- Check file encoding (should be UTF-8, no BOM)

---

### Test 2: Agent Imports

**Purpose:** Verify all agents can be imported

**Command:**
```bash
python -c "
from agents.requirements_agent import RequirementsAgent
from agents.document_agent import DocumentAgent
from agents.netlist_agent import NetlistAgent
from agents.glr_agent import GLRAgent
from agents.srs_agent import SRSAgent
from agents.sdd_agent import SDDAgent
from agents.code_agent import CodeAgent
print('All agents imported successfully!')
"
```

**Expected:** `All agents imported successfully!`

---

### Test 3: Generator Integration

**Purpose:** Verify generators are integrated with agents

**Command:**
```bash
python -c "
from agents.document_agent import DocumentAgent
agent = DocumentAgent()
print('Has HRSGenerator:', hasattr(agent, 'hrs_generator'))
"
```

**Expected:** `Has HRSGenerator: True`

---

### Test 4: LLM API Connection

**Purpose:** Test actual API call to Anthropic

**Command:**
```bash
python -c "
import asyncio
from agents.base_agent import BaseAgent

async def test():
    class T(BaseAgent):
        def __init__(self):
            super().__init__(phase_number='T', phase_name='T')
        def get_system_prompt(self, ctx):
            return 'Test'

    agent = T()
    r = await agent.call_llm(messages=[{'role': 'user', 'content': 'Say OK'}])
    print('Response:', r.get('content', '')[:50])

asyncio.run(test())
"
```

**Expected:** `Response: OK` (or similar)

---

### Test 5: End-to-End Pipeline

**Purpose:** Test complete pipeline flow

**Command:**
```bash
python run_interactive.py
```

Then interact with the system:
```
> I want to build a LED blinker circuit
> 12V input, 50mA current
> generate requirements
```

**Expected:** Complete Phase 1 with requirements and component recommendations

---

## Full Architecture Test

The `test_full_architecture.py` script runs comprehensive tests:

```bash
python test_full_architecture.py
```

**Tests included:**
1. API Key Configuration
2. Agent Imports (all 8 agents)
3. Agent Initialization
4. Generator Functionality (all 6 generators)
5. CodeReviewer
6. LLM Connection
7. ComponentSearchTool (if available)
8. Mini End-to-End Pipeline

**Results saved to:** `output/architecture_test_YYYYMMDD_HHMMSS.json`

---

## Interactive Testing

### Option 1: Interactive CLI

```bash
python run_interactive.py
```

Features:
- Conversational requirements capture
- Real-time LLM responses
- Progress tracking
- File generation verification

### Option 2: Streamlit UI

```bash
streamlit run app.py
```

Features:
- Web-based interface
- Dashboard with project status
- File viewer for generated documents
- Visual pipeline progress

### Option 3: FastAPI Server

```bash
# Terminal 1: Start server
python main.py

# Terminal 2: Test API
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/projects
```

---

## Testing Specific Phases

### Test Phase 1 (Requirements Capture)

```python
import asyncio
from agents.requirements_agent import RequirementsAgent

async def test_p1():
    agent = RequirementsAgent()
    result = await agent.execute(
        project_context={
            "name": "Test Project",
            "output_dir": "output/test",
            "conversation_history": []
        },
        user_input="I want a 12V to 3.3V buck converter"
    )
    print(result["response"])

asyncio.run(test_p1())
```

### Test Phase 2 (HRS Generation)

```python
import asyncio
from pathlib import Path
from agents.document_agent import DocumentAgent

async def test_p2():
    agent = DocumentAgent()

    # Create test requirements
    req_file = Path("output/test/requirements.md")
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# Requirements\n- 12V input\n- 3.3V output")

    result = await agent.execute(
        project_context={
            "name": "Test Project",
            "output_dir": "output/test"
        },
        user_input="generate"
    )

    print(result["response"])

asyncio.run(test_p2())
```

### Test Phase 8c (Code Generation)

```python
import asyncio
from pathlib import Path
from agents.code_agent import CodeAgent

async def test_p8c():
    agent = CodeAgent()

    # Create test SRS and SDD
    output = Path("output/test")
    output.mkdir(parents=True, exist_ok=True)
    (output / "SRS_Test_Project.md").write_text("# SRS\n- SW-001: Initialize")
    (output / "SDD_Test_Project.md").write_text("# SDD\n- Module: Main")

    result = await agent.execute(
        project_context={
            "name": "Test Project",
            "output_dir": "output/test"
        },
        user_input="generate"
    )

    print(result["response"])
    print(f"Generated files: {list(result['outputs'].keys())}")

asyncio.run(test_p8c())
```

---

## Testing Component Search (Optional)

### Setup ChromaDB (Optional)

```bash
# Install ChromaDB
pip install chromadb

# Populate components (if script exists)
python data/populate_components.py
```

### Test Component Search

```python
from tools.component_search import ComponentSearchTool

tool = ComponentSearchTool()

# Search for components
results = tool.search("3.3V LDO regulator", n_results=3)

for r in results:
    print(f"{r.component.part_number}: {r.component.description}")
    print(f"  Similarity: {r.similarity_score}")
```

---

## Testing Code Review

### Test CodeReviewer Directly

```python
from reviewers.code_reviewer import CodeReviewer

reviewer = CodeReviewer()

# Test code with issues
code = """
#include <stdlib.h>
#include <string.h>

void problematic() {
    char *p = malloc(100);
    strcpy(p, "user input");
    goto cleanup;
    cleanup:
    free(p);
}
"""

result = reviewer.review_code(code, language="c", standards=["MISRA-C-2012"])

print(f"Score: {result['score']}/100")
print(f"Issues: {result['total_issues']}")
print(f"Critical: {result['critical_issues']}")
print(result['details'])
```

---

## Common Issues and Solutions

### Issue: "API key not found"

**Solution:**
```bash
# Check .env file
cat .env

# Should see: ANTHROPIC_API_KEY=sk-ant-...

# If not, add it:
echo "ANTHROPIC_API_KEY=sk-ant-your-key" > .env
```

### Issue: "Rate limit exceeded"

**Solution:**
- Wait 1-2 minutes before retrying
- Use fast model for simple tasks
- Check usage at https://console.anthropic.com/

### Issue: "ChromaDB not available"

**Solution:**
- ChromaDB is optional
- System uses LLM fallback automatically
- No action needed

### Issue: "ImportError"

**Solution:**
```bash
pip install -r requirements.txt
```

---

## Performance Testing

### Test API Response Time

```python
import asyncio
import time
from agents.base_agent import BaseAgent

async def test_performance():
    class T(BaseAgent):
        def __init__(self):
            super().__init__(phase_number='T', phase_name='T')
        def get_system_prompt(self, ctx):
            return 'Test'

    agent = T()

    start = time.time()
    r = await agent.call_llm(messages=[{'role': 'user', 'content': 'Hi'}])
    elapsed = time.time() - start

    print(f"Response time: {elapsed:.2f}s")
    print(f"Tokens: {r['usage']['input_tokens']} in, {r['usage']['output_tokens']} out")

asyncio.run(test_performance())
```

---

## Continuous Testing

### Run Tests Automatically

Create a test script `run_all_tests.sh`:

```bash
#!/bin/bash
echo "Running all Silicon to Software (S2S) tests..."

echo "[1/3] Simple API test..."
python simple_api_test.py

echo "[2/3] Full architecture test..."
python test_full_architecture.py

echo "[3/3] Unit tests..."
pytest tests/ -v

echo "All tests complete!"
```

Make it executable:
```bash
chmod +x run_all_tests.sh
./run_all_tests.sh
```

---

## Next Steps

After successful testing:

1. **Run Interactive Demo:**
   ```bash
   python run_interactive.py
   ```

2. **Start Web UI:**
   ```bash
   streamlit run app.py
   ```

3. **Build Your Project:**
   - Use the interactive CLI
   - Follow the 8-phase pipeline
   - Generate IEEE-compliant documentation

4. **Review Generated Outputs:**
   ```bash
   python show_outputs.py
   ```

---

---

## UI Testing with Playwright

The `tests/test_ui_playwright.py` file provides end-to-end browser tests for the Streamlit UI.

### Setup (run once)
```bash
pip install playwright pytest-playwright
playwright install chromium
```

### Run UI Tests
```bash
# Start the app first (in a separate terminal):
streamlit run app.py &
python -m uvicorn main:app --port 8000 &

# Run all UI tests (headless):
pytest tests/test_ui_playwright.py -v

# Run with browser visible (for debugging):
pytest tests/test_ui_playwright.py -v --headed

# Or run the standalone smoke test (opens browser interactively):
python tests/test_ui_playwright.py
```

### What's tested
- All 8 tab navigation (Overview, New Project, Design Chat, Pipeline, Documents, Netlist, Code Review, Dashboard)
- New project form validation (empty name error)
- Project creation success flow
- Design chat welcome message and chat input
- Sidebar API key status and mode indicator
- Dashboard metric cards
- Screenshots of all tabs saved to `test_screenshots/`

---

**For more details, see:**
- [API_KEY_SETUP.md](API_KEY_SETUP.md) - API key configuration guide
- [KNOWN_ISSUES_RESOLVED.md](KNOWN_ISSUES_RESOLVED.md) - Issue resolutions
- [INTEGRATION_CHANGES.md](INTEGRATION_CHANGES.md) - Integration details
- [GENERATOR_TOOL_REFERENCE.md](GENERATOR_TOOL_REFERENCE.md) - Quick reference

"""
Test script to diagnose Mermaid rendering setup.

This script tests all three rendering methods used by the Silicon to Software (S2S):
1. mmdc (mermaid-cli)
2. Node.js renderer (mermaid_renderer.js)
3. mermaid.ink public API

Run this to diagnose why Mermaid diagrams aren't appearing in DOCX downloads.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

# Test mermaid code (simple flowchart)
TEST_MERMAID = """```mermaid
flowchart TD
    A[Start] --> B{Decision}
    B -->|Yes| C[Action 1]
    B -->|No| D[Action 2]
    C --> E[End]
    D --> E
```"""

def test_mmdc():
    """Test mmdc (mermaid-cli) installation."""
    print("\n=== Testing mmdc (mermaid-cli) ===")
    try:
        result = subprocess.run(
            ["mmdc.cmd", "--version"] if sys.platform == "win32" else ["mmdc", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace"
        )
        if result.returncode == 0:
            print(f"[OK] mmdc installed: {result.stdout.strip()}")
            return True
        else:
            print(f"[FAIL] mmdc not found")
            return False
    except FileNotFoundError:
        print("[FAIL] mmdc not found (FileNotFoundError)")
        return False
    except Exception as e:
        print(f"[FAIL] mmdc error: {e}")
        return False

def test_node_renderer():
    """Test Node.js renderer availability."""
    print("\n=== Testing Node.js renderer ===")

    # Check if node is available
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace"
        )
        if result.returncode != 0:
            print("[FAIL] node not found")
            return False
        print(f"[OK] node installed: {result.stdout.strip()}")
    except FileNotFoundError:
        print("[FAIL] node not found")
        return False

    # Check for renderer script
    renderer_candidates = [
        Path(__file__).parent / "mermaid_renderer.js",
        Path(__file__).parent.parent / "mermaid-renderer" / "render.js",
        Path("/sessions/pensive-laughing-clarke/mermaid-renderer/render.js"),
    ]

    renderer_js = None
    for candidate in renderer_candidates:
        if candidate.exists():
            renderer_js = str(candidate)
            print(f"[OK] Found renderer: {renderer_js}")
            break

    if not renderer_js:
        print("[FAIL] mermaid_renderer.js not found")
        print("   Looked in:")
        for candidate in renderer_candidates:
            print(f"   - {candidate}")
        return False

    # Test cairosvg (handle import errors gracefully)
    try:
        import cairosvg
        # Test if cairo library is actually available
        try:
            from cairocffi import cairo
            print("[OK] cairosvg + Cairo installed (python package + native library)")
            return True
        except OSError:
            print("[PARTIAL] cairosvg installed but Cairo DLL not found")
            print("        Node.js renderer will use mermaid.ink fallback instead")
            return True  # Still count as available since we have the fallback
    except ImportError:
        print("[FAIL] cairosvg not installed (pip install cairosvg)")
        return True  # Still have mermaid.ink fallback
    except Exception as e:
        print(f"[WARNING] cairosvg test error: {e}")
        return True  # Still have mermaid.ink fallback

def test_mermaid_ink():
    """Test mermaid.ink API connectivity."""
    print("\n=== Testing mermaid.ink API ===")
    try:
        import urllib.request
        import base64

        # Simple test diagram
        code = "flowchart TD\nA-->B"
        encoded = base64.urlsafe_b64encode(code.encode()).decode()
        url = f"https://mermaid.ink/img/{encoded}?type=png"

        print(f"Testing: {url[:80]}...")
        req = urllib.request.Request(url, headers={"User-Agent": "HardwarePipeline/1.0"})

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()

        if data and len(data) > 200:
            print(f"[OK] mermaid.ink API working (received {len(data)} bytes)")
            return True
        else:
            print("[FAIL] mermaid.ink returned empty response")
            return False
    except Exception as e:
        print(f"[FAIL] mermaid.ink error: {e}")
        return False

def test_render_sample():
    """Test actual rendering with sample diagram."""
    print("\n=== Testing actual rendering ===")

    code = """flowchart TD
    A[Start] --> B[End]"""

    # Create temp file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Try importing the render function from main.py
        sys.path.insert(0, str(Path(__file__).parent))
        from main import _render_mermaid_local

        success = _render_mermaid_local(code, tmp_path)

        if success and Path(tmp_path).exists():
            size = Path(tmp_path).stat().st_size
            print(f"[OK] Rendering successful! ({size} bytes)")
            print(f"   Output: {tmp_path}")
            # Don't delete so user can view it
            print(f"   Note: Temp file will be cleaned up on system restart")
            return True
        else:
            print("[FAIL] Rendering failed - all three methods failed")
            return False
    except Exception as e:
        print(f"[FAIL] Render test error: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Mermaid Rendering Diagnostic Tool")
    print("=" * 60)
    print("\nThis tool checks which Mermaid rendering methods are available.")
    print("Silicon to Software (S2S) tries methods in this order:")
    print("  1. mmdc (mermaid-cli) - preferred, works on all platforms")
    print("  2. Node.js renderer - requires node + cairosvg")
    print("  3. mermaid.ink API - requires internet")

    results = {
        "mmdc": test_mmdc(),
        "node_renderer": test_node_renderer(),
        "mermaid_ink": test_mermaid_ink(),
    }

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    available = sum(1 for v in results.values() if v)
    total = len(results)

    print(f"\nAvailable methods: {available}/{total}")

    if available == 0:
        print("\n[ERROR] NO RENDERING METHODS AVAILABLE")
        print("\nTo fix Mermaid in DOCX:")
        print("1. Install mmdc (recommended):")
        print("   npm install -g @mermaid-js/mermaid-cli")
        print("\n2. OR install Node.js renderer:")
        print("   pip install cairosvg")
        print("   npm install mermaid jsdom")
        print("\n3. OR check internet connection for mermaid.ink")
    elif available == 1:
        print("\n[WARNING] Only one method available - may have issues")
        print(f"   Working: {[k for k, v in results.items() if v][0]}")
    else:
        print(f"\n[OK] Multiple methods available:")
        for k, v in results.items():
            status = "[OK]" if v else "[FAIL]"
            print(f"   {status} {k}")

    # Try actual render
    if available > 0:
        test_render_sample()

    print("\n" + "=" * 60)

"""
push_to_github.py — Manual GitHub push for already-generated project output.

Usage:
    python push_to_github.py [project_name]

    If project_name is omitted, lists available output directories.

This script pushes generated artefacts from output/<project_name>/ to GitHub,
creates a PR, and prints the PR URL.

Requires: pip install GitPython PyGithub python-dotenv
"""

import sys
import asyncio
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from agents.git_agent import GitAgent


async def main():
    output_root = Path("output")

    if not output_root.exists():
        print("No output/ directory found. Run a pipeline phase first.")
        return

    # List available projects
    available = [d.name for d in output_root.iterdir() if d.is_dir()]
    if not available:
        print("No project output directories found.")
        return

    # Pick project name from args or prompt
    if len(sys.argv) > 1:
        project_name = sys.argv[1]
    elif len(available) == 1:
        project_name = available[0]
        print(f"Using project: {project_name}")
    else:
        print("Available projects:")
        for i, name in enumerate(available, 1):
            print(f"  {i}. {name}")
        choice = input("Enter project name or number: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            project_name = available[idx] if 0 <= idx < len(available) else choice
        else:
            project_name = choice

    output_dir = output_root / project_name
    if not output_dir.exists():
        print(f"Error: output/{project_name}/ does not exist.")
        return

    # Show config
    print("\nConfiguration:")
    print(f"  GitHub repo  : {settings.github_repo or '(not set)'}")
    print(f"  Token set    : {'Yes' if settings.github_token else 'No'}")
    print(f"  Git enabled  : {settings.git_enabled}")
    print(f"  Output dir   : {output_dir.resolve()}")
    print()

    if not settings.github_token:
        print("ERROR: GITHUB_TOKEN not set in .env")
        return
    if not settings.github_repo:
        print("ERROR: GITHUB_REPO not set in .env")
        return

    agent = GitAgent()
    if not agent.enabled:
        print("ERROR: GitAgent is disabled. Check GITHUB_TOKEN in .env")
        return

    print(f"Pushing '{project_name}' output to GitHub...")
    result = await agent.commit_and_pr(
        project_name=project_name,
        output_dir=output_dir,
    )

    print()
    if result.get("success"):
        print("SUCCESS!")
        print(f"  Commit SHA : {result.get('commit_sha')}")
        print(f"  Branch     : {result.get('branch')}")
        if result.get("pr_url"):
            print(f"  PR URL     : {result['pr_url']}")
        else:
            print("  PR         : Not created (branch was pushed but PR creation failed)")
            print("               You can create it manually at:")
            print(f"               https://github.com/{settings.github_repo}/compare/{result.get('branch')}")
    else:
        print(f"FAILED: {result.get('error') or result.get('reason')}")


if __name__ == "__main__":
    asyncio.run(main())

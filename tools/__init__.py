"""
Silicon to Software (S2S) - Tools
Utilities and tools available to agents for component search, scraping, calculations.
"""

from .calculator import CalculatorTool

__all__ = [
    "CalculatorTool",
]

# Optional imports — graceful degradation when dependencies missing
try:
    from .component_search import ComponentSearchTool
    __all__.append("ComponentSearchTool")
except ImportError:
    ComponentSearchTool = None  # type: ignore

try:
    from .web_scraper import WebScraperTool
    __all__.append("WebScraperTool")
except ImportError:
    WebScraperTool = None  # type: ignore

"""
Component Search Tool — semantic component lookup backed by Chroma.

Public API (unchanged):
    tool = ComponentSearchTool()
    results = tool.search("3.3V LDO regulator 1A low noise")
    tool.add_component(component, description_text)
    tool.get_by_part_number("STM32F407")
    tool.get_stats()

Internally this now uses `langchain-chroma` so we get:
  - metadata filters via a single dict arg
  - swappable embedders (OpenAI / HuggingFace / any LangChain `Embeddings`)
  - no hand-rolled embedding-function wiring

Keeps the same SQLite-on-disk Chroma store so existing data is compatible.
The `CHROMADB_AVAILABLE` / `_collection` attributes are preserved so callers
like `services.seed_components` still work unchanged.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

# Optional import — retrieval is allowed to be unavailable (air-gap demo).
try:
    from langchain_chroma import Chroma
    from langchain_core.embeddings import Embeddings
    LANGCHAIN_CHROMA_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001 — keep the pipeline usable even if deps missing
    LANGCHAIN_CHROMA_AVAILABLE = False
    Chroma = None  # type: ignore[assignment,misc]
    Embeddings = object  # type: ignore[assignment,misc]
    logging.warning(
        "langchain-chroma not available — component vector search disabled. "
        "Error: %s",
        _exc,
    )

# Back-compat flag: older callers check CHROMADB_AVAILABLE directly.
CHROMADB_AVAILABLE = LANGCHAIN_CHROMA_AVAILABLE

from config import settings
from schemas.component import Component, ComponentSearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Upstream filters (P26.7, 2026-05-05) — mirror the post-audit gates from
# `agents/red_team_audit.py` so obsolete and through-hole parts never reach
# the candidate pool the LLM picks from.
# ---------------------------------------------------------------------------

# Lazy import — avoid a hard dependency on `agents/` at module load (the
# component_search tool is also used by the seed scripts which don't need
# the audit chain). Falls back to an empty blacklist if the import fails.
try:
    from agents.red_team_audit import _is_stale_mpn as _audit_is_stale_mpn  # type: ignore
except Exception:
    def _audit_is_stale_mpn(pn: str) -> tuple[bool, str]:  # type: ignore
        return (False, "")

_NON_ACTIVE_LIFECYCLE = frozenset({
    "obsolete", "discontinued", "nrnd",
    "not-recommended", "not_recommended",
    "eol", "end-of-life", "end_of_life",
    "preview", "pre-production",  # not yet shippable for production
})

# Through-hole / non-SMT package keywords — match against `Component.package`
# (case-insensitive substring match). Anything containing one of these is
# excluded when `exclude_through_hole=True`.
_THROUGH_HOLE_PKG_KEYWORDS = (
    "dip", "pdip",          # dual in-line
    "to-220", "to220",      # TO-220 family
    "to-247", "to247",
    "to-3", "to3",
    "to-92", "to92",
    "axial", "radial",
    "through-hole", "through hole", "thru-hole", "thru hole", "pth",
    "pin-grid", "pga",      # pin-grid array (legacy)
    "sip",                  # single in-line
)


def _is_part_obsolete(c: Component) -> bool:
    """True iff the part is on the stale-MPN blacklist OR has a non-active
    lifecycle_status (case/whitespace tolerant)."""
    pn = (c.part_number or "").strip()
    if pn:
        stale, _reason = _audit_is_stale_mpn(pn)
        if stale:
            return True
    status = (c.lifecycle_status or "").strip().lower().replace(" ", "_")
    if status and status in _NON_ACTIVE_LIFECYCLE:
        return True
    return False


def _is_through_hole_package(pkg: Optional[str]) -> bool:
    """True iff the package string matches a known through-hole keyword.
    Empty/None packages return False (we can't tell — let the LLM decide,
    informed by the spec_hint prompt)."""
    if not pkg:
        return False
    p = pkg.lower()
    return any(kw in p for kw in _THROUGH_HOLE_PKG_KEYWORDS)


# ---------------------------------------------------------------------------
# Embedding-function selection
# ---------------------------------------------------------------------------

_PLACEHOLDER_KEYS = {"", "sk-xxxxx", "sk-proj-xxxxx", "your-key-here"}


class _ChromaDefaultEmbeddings:
    """Adapter over chromadb's bundled ONNX embedder so we don't need
    sentence-transformers installed in air-gap / slim deployments.

    chromadb ships an ONNX runtime of all-MiniLM-L6-v2 — same model HF
    would serve, but without a Torch dependency. This matches the
    pre-refactor default behaviour.
    """

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import (
            DefaultEmbeddingFunction,
        )
        self._fn = DefaultEmbeddingFunction()

    @staticmethod
    def _to_floats(vec) -> List[float]:
        # ONNX returns numpy float32; chromadb's upsert validates strictly
        # and rejects np.float32 — coerce to native Python floats.
        return [float(x) for x in vec]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._to_floats(v) for v in self._fn(list(texts))]

    def embed_query(self, text: str) -> List[float]:
        return self._to_floats(self._fn([text])[0])


def _build_embeddings() -> Optional["Embeddings"]:
    """Pick the best available embedder.

    Priority:
      1. OpenAI (if OPENAI_API_KEY is a real key)           — best recall
      2. HuggingFace all-MiniLM-L6-v2                        — if installed
      3. ChromaDB bundled ONNX all-MiniLM-L6-v2              — air-gap default
    """
    key = (settings.openai_api_key or "").strip()
    if key and key not in _PLACEHOLDER_KEYS:
        try:
            from langchain_openai import OpenAIEmbeddings
            logger.info("component_search.embeddings=openai model=%s",
                        settings.embedding_model)
            return OpenAIEmbeddings(
                model=settings.embedding_model,
                api_key=key,
            )
        except Exception as exc:
            logger.warning("OpenAIEmbeddings unavailable (%s) — falling back", exc)

    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        logger.info("component_search.embeddings=huggingface model=all-MiniLM-L6-v2")
        return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    except Exception as exc:
        logger.info("HuggingFaceEmbeddings unavailable (%s) — trying ONNX default", exc)

    try:
        logger.info("component_search.embeddings=chromadb_default_onnx")
        return _ChromaDefaultEmbeddings()  # type: ignore[return-value]
    except Exception as exc:
        logger.warning("ChromaDefault embedder unavailable (%s) — search disabled", exc)
        return None


# ---------------------------------------------------------------------------
# ComponentSearchTool
# ---------------------------------------------------------------------------

class ComponentSearchTool:
    """Semantic component search backed by langchain-chroma.

    Thread-safety: instances are cheap, create one per request if needed.
    The underlying Chroma PersistentClient is already thread-safe for reads.
    """

    def __init__(self, embeddings: Optional["Embeddings"] = None):
        self._vs: Optional["Chroma"] = None
        # Back-compat: the old implementation exposed `_collection` for callers
        # that wanted raw counts. We expose the same attribute pointing at the
        # underlying chromadb collection Chroma wraps.
        self._collection = None
        self._initialize(embeddings)

    # ------------------------------------------------------------------ init

    def _initialize(self, embeddings: Optional["Embeddings"]) -> None:
        if not LANGCHAIN_CHROMA_AVAILABLE:
            logger.warning(
                "langchain-chroma not available — the pipeline will still run; "
                "component selection uses LLM knowledge instead."
            )
            return
        try:
            persist_dir = Path(settings.chroma_persist_dir)
            persist_dir.mkdir(parents=True, exist_ok=True)

            emb = embeddings or _build_embeddings()
            if emb is None:
                logger.warning("No embedding function available — search disabled")
                return

            self._vs = Chroma(
                collection_name=settings.chroma_collection_name,
                persist_directory=str(persist_dir),
                embedding_function=emb,
                collection_metadata={"hnsw:space": "cosine"},
            )
            # LangChain's Chroma wrapper exposes the raw chromadb collection
            # via `_collection` too — surface it so existing callers work.
            self._collection = getattr(self._vs, "_collection", None)
            try:
                count = self._collection.count() if self._collection else 0
                logger.info("ComponentSearchTool ready: %d components cached", count)
            except Exception:
                logger.info("ComponentSearchTool ready (count unavailable)")
        except Exception as exc:
            logger.warning("ComponentSearchTool initialisation failed: %s", exc)
            self._vs = None
            self._collection = None

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _flatten_metadata(component: Component) -> dict:
        """Build Chroma-safe metadata (only str/int/float/bool/None).

        key_specs is JSON-serialised to a single column so it round-trips.
        """
        meta: dict = {
            "part_number": component.part_number,
            "manufacturer": component.manufacturer,
            "description": component.description,
            "category": component.category or "Unknown",
            "datasheet_url": component.datasheet_url or "",
            "lifecycle_status": component.lifecycle_status or "unknown",
            # P26.7 (2026-05-05): persist the package so the search filter can
            # exclude through-hole parts at retrieval time without round-
            # tripping the full Component object.
            "package": component.package or "",
        }
        if component.estimated_cost_usd is not None:
            meta["estimated_cost_usd"] = float(component.estimated_cost_usd)
        if isinstance(component.key_specs, dict):
            meta["key_specs_json"] = json.dumps(component.key_specs)
        return meta

    @staticmethod
    def _component_from_metadata(metadata: dict) -> Component:
        key_specs: dict = {}
        raw = metadata.get("key_specs_json")
        if isinstance(raw, str):
            try:
                key_specs = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                key_specs = {}
        return Component(
            part_number=metadata.get("part_number", ""),
            manufacturer=metadata.get("manufacturer", ""),
            description=metadata.get("description", ""),
            category=metadata.get("category", "Unknown"),
            key_specs=key_specs,
            package=metadata.get("package", "") or "",
            datasheet_url=metadata.get("datasheet_url", "") or None,
            lifecycle_status=metadata.get("lifecycle_status", "unknown"),
            estimated_cost_usd=metadata.get("estimated_cost_usd"),
        )

    # ------------------------------------------------------------------- API

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        n_results: int = 5,
        min_similarity: float = 0.6,
        exclude_obsolete: bool = True,
        exclude_through_hole: bool = True,
    ) -> List[ComponentSearchResult]:
        """Semantic search for components. Returns top-k above the threshold.

        P26.7 (2026-05-05): added two upstream filters so the LLM never sees
        obsolete or through-hole candidates.
          • ``exclude_obsolete`` (default True) drops parts whose
            ``lifecycle_status`` is anything but "active"/"unknown" AND any
            part on the stale-MPN blacklist from `red_team_audit`.
          • ``exclude_through_hole`` (default True) drops parts whose package
            text contains a TH-only keyword (DIP / PDIP / TO-220 / axial /
            radial / through-hole / PTH).
        Pre-fix, the lifecycle gate fired only as a post-audit, so the BOM
        could ship with NRND/EOL parts; through-hole parts had no filter at
        all and slipped past whenever the LLM picked one.
        """
        if not self._vs:
            logger.warning("ComponentSearchTool not available, returning []")
            return []
        try:
            flt: Optional[dict] = {"category": category} if category else None
            # Over-fetch so post-filters can drop bad candidates and still
            # return n_results. 4× headroom is enough in practice.
            docs_scores = self._vs.similarity_search_with_relevance_scores(
                query, k=n_results * 4, filter=flt,
            )
            out: List[ComponentSearchResult] = []
            for doc, score in docs_scores:
                if score < min_similarity:
                    continue
                component = self._component_from_metadata(doc.metadata or {})
                if exclude_obsolete and _is_part_obsolete(component):
                    logger.debug(
                        "component_search.skip.obsolete pn=%s status=%s",
                        component.part_number, component.lifecycle_status,
                    )
                    continue
                if exclude_through_hole and _is_through_hole_package(component.package):
                    logger.debug(
                        "component_search.skip.through_hole pn=%s pkg=%s",
                        component.part_number, component.package,
                    )
                    continue
                out.append(ComponentSearchResult(
                    component=component,
                    relevance_score=round(float(score), 3),
                    match_reason=doc.page_content or "",
                ))
                if len(out) >= n_results:
                    break
            logger.info(
                "Component search '%s': %d results (obsolete_filter=%s, smt_only=%s)",
                query, len(out), exclude_obsolete, exclude_through_hole,
            )
            return out
        except Exception as exc:
            logger.error("Component search failed: %s", exc)
            return []

    def add_component(self, component: Component, description_text: str) -> bool:
        """Upsert a component by part_number. Returns True on success."""
        if not self._vs:
            return False
        try:
            self._vs.add_texts(
                texts=[description_text],
                metadatas=[self._flatten_metadata(component)],
                ids=[component.part_number],
            )
            logger.debug("Added/updated component: %s", component.part_number)
            return True
        except Exception as exc:
            logger.error("Failed to add component %s: %s", component.part_number, exc)
            return False

    def get_by_part_number(self, part_number: str) -> Optional[Component]:
        if not self._collection:
            return None
        try:
            res = self._collection.get(ids=[part_number], include=["metadatas"])
            metas = res.get("metadatas") or []
            if metas and metas[0]:
                return self._component_from_metadata(metas[0])
        except Exception as exc:
            logger.error("Failed to get %s: %s", part_number, exc)
        return None

    def get_stats(self) -> dict:
        """Return {total_components, categories: {name: count}}."""
        if not self._collection:
            return {"total_components": 0, "categories": {}}
        try:
            data = self._collection.get(include=["metadatas"])
            total = len(data.get("ids") or [])
            categories: dict = {}
            for meta in data.get("metadatas") or []:
                cat = (meta or {}).get("category", "Unknown")
                categories[cat] = categories.get(cat, 0) + 1
            return {"total_components": total, "categories": categories}
        except Exception as exc:
            logger.error("Failed to get stats: %s", exc)
            return {"total_components": 0, "categories": {}}

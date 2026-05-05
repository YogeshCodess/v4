"""
DocConverter — Markdown to docx/pdf via pandoc.

Usage:
    converter = DocConverter()
    path = converter.to_docx(md_content, "HRS_MyProject", output_dir)
    path = converter.to_pdf(md_content, "HRS_MyProject", output_dir)
    path = converter.convert(md_content, "HRS_MyProject", output_dir, fmt="docx")

Requires pandoc installed and on PATH.
Falls back gracefully (returns None) if pandoc is unavailable.
"""

from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import Literal, Optional

log = logging.getLogger(__name__)

OutputFormat = Literal["docx", "pdf", "html"]

_PANDOC_AVAILABLE: Optional[bool] = None  # lazy check

# ── Mermaid → PNG via mermaid.ink (free public renderer) ────────────────────
_MERMAID_FENCE = re.compile(
    r'```mermaid\s*\n(.*?)\n```',
    re.DOTALL | re.IGNORECASE,
)

def _mermaid_to_png(diagram_code: str, tmp_dir: Path, idx: int) -> Optional[Path]:
    """
    Render a mermaid diagram to a PNG file using mermaid.ink public API.
    Returns the path to the saved PNG, or None if the render fails.
    """
    try:
        encoded = base64.urlsafe_b64encode(diagram_code.encode("utf-8")).decode("ascii")
        url = f"https://mermaid.ink/img/{encoded}?type=png"
        req = urllib.request.Request(url, headers={"User-Agent": "HardwarePipeline/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if not data or len(data) < 100:
            return None
        out = tmp_dir / f"mermaid_{idx}.png"
        out.write_bytes(data)
        log.info("mermaid.rendered idx=%d size=%d", idx, len(data))
        return out
    except Exception as exc:
        log.warning("mermaid.render_failed idx=%d: %s", idx, exc)
        return None


def _preprocess_mermaid(markdown_content: str, tmp_dir: Path) -> str:
    """
    Replace ```mermaid ... ``` blocks with either:
    - An inline image reference (if mermaid.ink is reachable), or
    - A styled ASCII fallback heading + code block.
    """
    idx = 0

    def replace_block(m: re.Match) -> str:
        nonlocal idx
        code = m.group(1).strip()
        idx += 1
        png_path = _mermaid_to_png(code, tmp_dir, idx)
        if png_path:
            # Pandoc will embed the PNG as an image in the docx
            return f"\n\n**System Architecture Diagram {idx}**\n\n![Diagram {idx}]({png_path})\n\n"
        else:
            # Fallback: labelled code block (visible in docx, not rendered)
            return (
                f"\n\n**System Architecture Diagram {idx}** *(source — open in Mermaid viewer)*\n\n"
                f"```\n{code}\n```\n\n"
            )

    return _MERMAID_FENCE.sub(replace_block, markdown_content)


def _check_pandoc() -> bool:
    global _PANDOC_AVAILABLE
    if _PANDOC_AVAILABLE is None:
        _PANDOC_AVAILABLE = shutil.which("pandoc") is not None
        if not _PANDOC_AVAILABLE:
            log.warning("pandoc not found on PATH — doc conversion disabled")
    return _PANDOC_AVAILABLE


class DocConverter:
    """Converts markdown content to docx/pdf using pandoc."""

    def convert(
        self,
        markdown_content: str,
        stem: str,
        output_dir: Path | str,
        fmt: OutputFormat = "docx",
        reference_doc: Optional[Path] = None,
    ) -> Optional[Path]:
        """
        Convert markdown to the target format via pandoc.

        Args:
            markdown_content: Source markdown text.
            stem: Output filename without extension (e.g., "HRS_MyProject").
            output_dir: Directory to write the output file.
            fmt: "docx" | "pdf" | "html"
            reference_doc: Optional .docx reference template for styling.

        Returns:
            Path to the generated file, or None if pandoc is unavailable / fails.
        """
        if not _check_pandoc():
            return None

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"{stem}.{fmt}"

        # Pre-process mermaid blocks → PNG images (kept alive until pandoc finishes)
        with tempfile.TemporaryDirectory() as img_tmp:
            img_tmp_path = Path(img_tmp)
            processed_md = _preprocess_mermaid(markdown_content, img_tmp_path)

            tmp_path: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", encoding="utf-8", delete=False
                ) as tmp:
                    tmp.write(processed_md)
                    tmp_path = Path(tmp.name)

                cmd = [
                    "pandoc",
                    str(tmp_path),
                    "-o", str(out_path),
                    "--standalone",
                    "--toc",
                    "--toc-depth=3",
                    # Allow pandoc to embed image files referenced in the markdown
                    "--resource-path", str(img_tmp_path),
                ]

                if fmt == "docx" and reference_doc and reference_doc.exists():
                    cmd += ["--reference-doc", str(reference_doc)]

                if fmt == "pdf":
                    cmd += ["--pdf-engine=xelatex"]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if result.returncode != 0:
                    log.error(
                        "pandoc failed (rc=%d): %s", result.returncode, result.stderr[:500]
                    )
                    return None

                log.info("doc_converter.ok fmt=%s path=%s", fmt, out_path)
                return out_path

            except subprocess.TimeoutExpired:
                log.error("pandoc timed out converting %s", stem)
                return None
            except Exception as exc:
                log.exception("doc_converter.error stem=%s fmt=%s: %s", stem, fmt, exc)
                return None
            finally:
                if tmp_path:
                    tmp_path.unlink(missing_ok=True)

    def to_docx(
        self,
        markdown_content: str,
        stem: str,
        output_dir: Path | str,
        reference_doc: Optional[Path] = None,
    ) -> Optional[Path]:
        return self.convert(markdown_content, stem, output_dir, "docx", reference_doc)

    def to_pdf(
        self,
        markdown_content: str,
        stem: str,
        output_dir: Path | str,
    ) -> Optional[Path]:
        return self.convert(markdown_content, stem, output_dir, "pdf")

    def to_html(
        self,
        markdown_content: str,
        stem: str,
        output_dir: Path | str,
    ) -> Optional[Path]:
        return self.convert(markdown_content, stem, output_dir, "html")

    def is_available(self) -> bool:
        return _check_pandoc()

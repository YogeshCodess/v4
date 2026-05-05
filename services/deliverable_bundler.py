"""
deliverable_bundler.py - assembles the final user-facing deliverable.

Layout produced (per Item #7/#8):

    <output_dir>/deliverable/<ProjectName>/
        Requirements & Component Selection/
            requirements.docx, requirements.pdf
            block_diagram.docx, block_diagram.pdf
            architecture.docx, architecture.pdf
            component_recommendations.docx, component_recommendations.pdf
            power_calculation.docx, power_calculation.pdf
            gain_loss_budget.docx, gain_loss_budget.pdf
            cascade_analysis.json   (binary - copied as-is)
        HRS/
            HRS.docx, HRS.pdf
        Compliance/
            compliance_report.docx, compliance_report.pdf
            compliance_matrix.csv (copied as-is)
        Netlist/
            netlist_visual.docx, .pdf
            drc_report.docx, .pdf
        GLR/
            glr_specification.docx, .pdf
        FPGA/
            fpga_design_report.docx, .pdf
            rtl/  (symlink/copy of the source files)
        Register Map/
            register_description_table.docx, .pdf
            programming_sequence.docx, .pdf
        SRS/
            SRS.docx, .pdf
        SDD/
            SDD.docx, .pdf
        Code Review/
            code_review_report.docx, .pdf
            drivers/   (verbatim source copy)
            qt_gui/    (verbatim source copy)
        raw/
            Phase_1/{requirements.md, block_diagram.md, ...}
            Phase_2/{HRS.md}
            Phase_3/{compliance_report.md, ...}
            Phase_4/{netlist_visual.md, drc_report.md}
            Phase_6/{glr_specification.md}
            Phase_7/{fpga_design_report.md, rtl/}
            Phase_7a/{register_description_table.md, programming_sequence.md}
            Phase_8a/{SRS.md}
            Phase_8b/{SDD.md}
            Phase_8c/{code_review_report.md, drivers/, qt_gui/}

Files that aren't markdown (e.g. .json, .csv, .v, .vhd, .h, .c, .cpp,
.ui, .pro, .xdc) are copied verbatim into the same per-phase folder so
the user can open them directly.

The bundler is idempotent: re-running on an existing deliverable folder
overwrites the per-phase docs but does not remove user-added files.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from services.doc_export import md_to_docx, md_to_pdf

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase routing - which phase each output file belongs to.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhaseSpec:
    phase_id: str
    raw_dirname: str          # raw/Phase_*  folder
    deliverable_dirname: str  # human-readable folder
    md_files: tuple[str, ...]   # markdown files this phase owns
    extras: tuple[str, ...] = ()  # other files (.json, .csv) to copy verbatim
    asset_dirs: tuple[str, ...] = ()  # subdirectories to copy verbatim


_PHASES: tuple[PhaseSpec, ...] = (
    PhaseSpec("P1", "Phase_1", "Requirements & Component Selection",
              md_files=(
                  "requirements.md", "block_diagram.md", "architecture.md",
                  "component_recommendations.md", "power_calculation.md",
                  "gain_loss_budget.md",
              ),
              extras=("cascade_analysis.json",)),
    PhaseSpec("P2", "Phase_2", "HRS",
              md_files=()),  # discovered dynamically
    PhaseSpec("P3", "Phase_3", "Compliance",
              md_files=("compliance_report.md",),
              extras=("compliance_matrix.csv",)),
    PhaseSpec("P4", "Phase_4", "Netlist",
              md_files=("netlist_visual.md", "drc_report.md"),
              extras=("netlist.json", "netlist_validation.json")),
    PhaseSpec("P6", "Phase_6", "GLR",
              md_files=("glr_specification.md",)),  # also dynamic GLR_*.md
    PhaseSpec("P7", "Phase_7", "FPGA",
              md_files=("fpga_design_report.md",),
              asset_dirs=("rtl",)),
    PhaseSpec("P7a", "Phase_7a", "Register Map",
              md_files=("register_description_table.md", "programming_sequence.md")),
    PhaseSpec("P8a", "Phase_8a", "SRS",
              md_files=()),
    PhaseSpec("P8b", "Phase_8b", "SDD",
              md_files=()),
    PhaseSpec("P8c", "Phase_8c", "Code Review",
              md_files=("code_review_report.md", "git_summary.md",
                        "ci_validation_report.md"),
              asset_dirs=("drivers", "qt_gui", ".github")),
)


def _resolve_phase_md(phase: PhaseSpec, output_dir: Path) -> list[Path]:
    """Return existing MD files for a phase, including dynamically-named ones."""
    found: list[Path] = []
    for fname in phase.md_files:
        p = output_dir / fname
        if p.exists() and p.is_file():
            found.append(p)
    # Dynamic names: HRS_<project>.md, SRS_<project>.md, etc.
    dyn_prefix = {
        "P2":  "HRS_",
        "P6":  "GLR_",
        "P8a": "SRS_",
        "P8b": "SDD_",
    }.get(phase.phase_id)
    if dyn_prefix:
        for p in output_dir.glob(f"{dyn_prefix}*.md"):
            if p.is_file() and p not in found:
                found.append(p)
    return found


# ---------------------------------------------------------------------------
# Bundler
# ---------------------------------------------------------------------------


@dataclass
class BundleReport:
    project_name: str
    deliverable_root: Path
    docs_written: int = 0
    pdfs_written: int = 0
    raw_files_copied: int = 0
    asset_dirs_copied: int = 0
    skipped: list[str] = None  # type: ignore

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "deliverable_root": str(self.deliverable_root),
            "docs_written": self.docs_written,
            "pdfs_written": self.pdfs_written,
            "raw_files_copied": self.raw_files_copied,
            "asset_dirs_copied": self.asset_dirs_copied,
            "skipped": self.skipped or [],
        }


def _safe_dir(name: str) -> str:
    """Project-name -> filesystem-safe leaf (preserves spaces, removes
    characters Windows refuses)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ").strip() or "Project"


def _copy_asset_dir(src: Path, dst: Path) -> int:
    """Copy a directory tree, replacing if it exists. Returns file count.

    Skips internal cache folders (`.docx_cache/`, `__pycache__/`,
    `.git/`) and editor scratch files (`*.swp`, `*.tmp`) so they don't
    leak into the user-facing deliverable. P26 (2026-05-04): added
    `.docx_cache` skip after the rxx P8c bundle was found shipping
    `qt_gui/.docx_cache/README.v8.docx` inside the export ZIP."""
    if not src.exists():
        return 0
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    _SKIP_DIRS = {".docx_cache", "__pycache__", ".git", "node_modules"}
    _SKIP_PATTERNS = ("*.swp", "*.tmp", "*.pyc", "*~")
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns(*_SKIP_DIRS, *_SKIP_PATTERNS),
    )
    return sum(1 for _ in dst.rglob("*") if _.is_file())


def build_deliverable(
    *,
    output_dir: Path | str,
    project_name: str,
    deliverable_root: Optional[Path | str] = None,
) -> BundleReport:
    """Walk `output_dir` (the per-project agent output) and assemble the
    deliverable bundle. If `deliverable_root` is None we drop it under
    `<output_dir>/deliverable/<safe_project_name>/`.
    """
    output_dir = Path(output_dir)
    safe = _safe_dir(project_name)
    if deliverable_root is None:
        deliverable_root = output_dir / "deliverable" / safe
    deliverable_root = Path(deliverable_root)
    deliverable_root.mkdir(parents=True, exist_ok=True)

    raw_root = deliverable_root / "raw"
    raw_root.mkdir(exist_ok=True)

    report = BundleReport(
        project_name=project_name,
        deliverable_root=deliverable_root,
        skipped=[],
    )

    for phase in _PHASES:
        per_dir = deliverable_root / phase.deliverable_dirname
        raw_dir = raw_root / phase.raw_dirname
        per_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        for md in _resolve_phase_md(phase, output_dir):
            # 1. Copy raw .md
            shutil.copy2(md, raw_dir / md.name)
            report.raw_files_copied += 1

            # 2. Render .docx
            stem = md.stem
            docx = per_dir / f"{stem}.docx"
            try:
                if md_to_docx(md, docx, title=stem.replace("_", " ").title()):
                    report.docs_written += 1
                else:
                    report.skipped.append(f"{phase.phase_id}/{md.name} (.docx failed)")
            except Exception as e:
                log.warning("deliverable.docx_failed phase=%s file=%s: %s",
                            phase.phase_id, md.name, e)
                report.skipped.append(f"{phase.phase_id}/{md.name} (.docx error: {e})")

            # 3. Render .pdf
            pdf = per_dir / f"{stem}.pdf"
            try:
                if md_to_pdf(md, pdf, title=stem.replace("_", " ").title()):
                    report.pdfs_written += 1
                else:
                    report.skipped.append(f"{phase.phase_id}/{md.name} (.pdf failed)")
            except Exception as e:
                log.warning("deliverable.pdf_failed phase=%s file=%s: %s",
                            phase.phase_id, md.name, e)
                report.skipped.append(f"{phase.phase_id}/{md.name} (.pdf error: {e})")

        # Extras: copy verbatim into BOTH the deliverable folder and the raw folder.
        for extra in phase.extras:
            src = output_dir / extra
            if src.exists() and src.is_file():
                shutil.copy2(src, per_dir / src.name)
                shutil.copy2(src, raw_dir / src.name)
                report.raw_files_copied += 1

        # Asset directories (rtl/, drivers/, qt_gui/, ...)
        for asset in phase.asset_dirs:
            src_asset = output_dir / asset
            if src_asset.exists() and src_asset.is_dir():
                _copy_asset_dir(src_asset, per_dir / asset)
                _copy_asset_dir(src_asset, raw_dir / asset)
                report.asset_dirs_copied += 1

    log.info(
        "deliverable.built project=%s root=%s docs=%d pdfs=%d raws=%d assets=%d",
        project_name, deliverable_root, report.docs_written, report.pdfs_written,
        report.raw_files_copied, report.asset_dirs_copied,
    )
    return report

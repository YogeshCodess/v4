"""
Silicon to Software (S2S) — Streamlit UI (Premium Design)

Architecture rule: this file contains ONLY rendering code.
- No agent imports, no asyncio.run(), no direct DB access.
- All business logic lives in services/ (ProjectService, ChatService, PipelineService).
- State transitions go through the FastAPI backend (/api/v1/...).
- Phase status is always read fresh from DB via the API; session_state is display cache only.
- Pipeline execution is fire-and-forget via POST /pipeline/run; UI polls /status.
"""

import json
import logging
import re
import time
from pathlib import Path

import httpx
import streamlit as st

from config import settings
from logging_config import configure_logging

configure_logging()
log = logging.getLogger("hardware_pipeline.ui")

# Mermaid rendering — graceful fallback
try:
    import streamlit_mermaid as stmd
    MERMAID_AVAILABLE = True
except ImportError:
    MERMAID_AVAILABLE = False

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Silicon to Software (S2S) — AI Design Studio",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# NOTE: Full state restoration happens inside _restore_state_from_url() at the
# top of main(). Do NOT do partial restoration here — it causes the early-return
# bug in _restore_state_from_url() by setting project_id without the rest.

# ── CSS + Google Fonts (always fresh — no cache so edits take effect) ─────────
def _load_css():
    css_file = Path(__file__).parent / "static" / "style.css"
    fonts = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:'
        'ital,wght@0,300;0,400;0,600;1,300;1,400&family=JetBrains+Mono:'
        'wght@300;400;500;700&family=Raleway:wght@300;400;500;700;900&display=swap" rel="stylesheet">'
    )
    css = ""
    if css_file.exists():
        css = f"<style>{css_file.read_text(encoding='utf-8')}</style>"
    return fonts + css

# st.html() is the correct way in Streamlit 1.31+ — renders HTML without showing text
st.html(_load_css())
# Ambient glow blobs — match the reference HTML design
st.html("""
<div style="position:fixed;width:600px;height:400px;top:-100px;left:-100px;
  background:radial-gradient(ellipse,rgba(201,168,76,0.06) 0%,transparent 70%);
  filter:blur(80px);pointer-events:none;z-index:0;border-radius:50%;"></div>
<div style="position:fixed;width:500px;height:500px;bottom:0;right:0;
  background:radial-gradient(ellipse,rgba(79,195,247,0.04) 0%,transparent 70%);
  filter:blur(80px);pointer-events:none;z-index:0;border-radius:50%;"></div>
""")


# ── Constants ─────────────────────────────────────────────────────────────────

# Phase metadata: (id, display_num, short_name, description, auto_run)
PHASE_META = [
    ("P1",  "1",   "Design & Requirements",    "AI-powered design chat — block diagram + requirements capture",   True),
    ("P2",  "2",   "HRS Document",             "IEEE 29148 Hardware Requirements Specification",                   True),
    ("P3",  "3",   "Compliance Check",          "RoHS / REACH / FCC / MIL-STD rules engine",                       True),
    ("P4",  "4",   "Netlist Generation",        "Visual connectivity graph with DRC checks",                       True),
    ("P5",  "5",   "PCB Layout",               "Manual — Gerber/ODB++ export ready",                               False),
    ("P6",  "6",   "GLR Specification",         "Glue Logic Requirements for FPGA/CPLD",                           True),
    ("P7",  "7",   "FPGA Design",              "Manual — RTL/synthesis ready",                                     False),
    ("P8a", "8a",  "SRS Document",              "IEEE 830 Software Requirements Specification",                    True),
    ("P8b", "8b",  "SDD Document",              "IEEE 1016 Software Design Description",                           True),
    ("P8c", "8c",  "Code + Review",             "C/C++ drivers, test suites, AST review",                          True),
]

_API = settings.api_base_url

# ── Shared httpx client ───────────────────────────────────────────────────────
@st.cache_resource
def _get_http_client():
    return httpx.Client(timeout=10.0, base_url=_API)


# ── API helpers ───────────────────────────────────────────────────────────────

def _api_get(path: str, timeout: float = 10.0):
    try:
        r = _get_http_client().get(path, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        return None
    except Exception as exc:
        log.warning("api.get_failed path=%s: %s", path, exc)
        return None


def _api_post(path: str, body: dict, timeout: float = 180.0):
    try:
        r = _get_http_client().post(path, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        return None
    except Exception as exc:
        log.warning("api.post_failed path=%s: %s", path, exc)
        return None


def _load_project(project_id: int):
    return _api_get(f"/api/v1/projects/{project_id}")


def _load_status(project_id: int) -> dict:
    """Load phase statuses — always fresh from DB via API."""
    data = _api_get(f"/api/v1/projects/{project_id}/status") or {}
    return data.get("phase_statuses", {})


def _phase_status(statuses: dict, pid: str) -> str:
    # Session-state override for "completed" always wins — this bridges the gap
    # between async DB writes and sync reads (e.g. Phase 1 just finished but
    # the DB hasn't been polled yet, or DB still shows 'draft_pending').
    override = st.session_state.get("_phase_overrides", {}).get(pid)
    if override == "completed":
        return "completed"
    # DB value is authoritative for non-pending states
    db_status = statuses.get(pid, {}).get("status", "pending")
    if db_status != "pending":
        return db_status
    # Fall back to session-state override (optimistic UI within the same session)
    return override or "pending"


# ── Mermaid utilities ─────────────────────────────────────────────────────────

def _sanitize_mermaid(code: str) -> str:
    code = re.sub(r'^```mermaid\s*', '', code.strip())
    code = re.sub(r'\s*```$', '', code).strip()
    for ch, rep in [
        ('\u2192','->'), ('\u2190','<-'), ('\u2194','<->'),
        ('\u00b1','+/-'), ('\u00d7','x'), ('\u00f7','/'),
        ('\u00b0','deg'), ('\u00b5','u'), ('\u03a9','ohm'),
        ('\u2264','<='), ('\u2265','>='), ('\u2260','!='),
        ('\u201c','"'), ('\u201d','"'), ('\u2018',"'"), ('\u2019',"'"),
        ('\u2013','-'), ('\u2014','-'),
    ]:
        code = code.replace(ch, rep)
    code = re.sub(r'subgraph\s+(\w+)\s*\["([^"]+)"\]', r'subgraph \1[\2]', code)
    code = re.sub(r'\[([^\]]*?)&([^\]]*?)\]',
                  lambda m: '[' + m.group(1) + 'and' + m.group(2) + ']', code)
    code = re.sub(r'\|"([^"]+)"\|',
                  lambda m: '|' + m.group(1).replace(':', ' -') + '|', code)
    # Clean node label content — angle brackets and raw parens break Mermaid v10 parser
    def _clean_label(m):
        inner = m.group(1)
        inner = re.sub(r'[<>]', '', inner)           # remove < >
        inner = re.sub(r'\(([^)]*)\)', r'\1', inner) # strip parens, keep content
        inner = inner.replace(':', ' -')             # colons in labels confuse parser
        return '[' + inner.strip() + ']'
    code = re.sub(r'\[([^\[\]\n]{1,120})\]', _clean_label, code)
    lines = [ln for ln in code.split('\n') if ln.strip() and not ln.strip().startswith('%%')]
    return '\n'.join(lines)


_MERMAID_VALID_STARTS = (
    'graph ', 'flowchart ', 'sequencediagram', 'classdiagram',
    'statediagram', 'erdiagram', 'journey', 'gantt', 'pie',
    'gitgraph', 'mindmap', 'timeline', 'xychart-beta',
    'graph tb', 'graph td', 'graph lr', 'graph rl',
)

def _is_valid_mermaid(code: str) -> bool:
    first = code.strip().split('\n')[0].strip().lower()
    return any(first.startswith(s) for s in _MERMAID_VALID_STARTS)


def _render_mermaid(code: str, key: str = None):
    code = _sanitize_mermaid(code)
    if not code:
        return
    if not _is_valid_mermaid(code):
        # Try to guess diagram type from content
        if '->' in code or '-->' in code:
            code = 'graph TD\n' + code
        else:
            st.code(code, language="text")
            return
    if MERMAID_AVAILABLE:
        try:
            stmd.st_mermaid(code, key=key)
            return
        except Exception:
            pass
    st.code(code, language="mermaid")


_KNOWN_HTML_TAGS = {
    'div','span','p','a','strong','em','b','i','u','s','ul','ol','li',
    'table','tr','td','th','thead','tbody','tfoot','caption',
    'br','hr','h1','h2','h3','h4','h5','h6','pre','code','blockquote',
    'img','input','form','button','select','option','style','script',
    'section','article','header','footer','main','nav','aside',
    'details','summary','mark','small','sub','sup',
}

def _strip_llm_xml(content: str) -> str:
    """Strip XML-style tags emitted by LLMs (output wrappers, field tags, safety flags).
    Preserves standard HTML tags so unsafe_allow_html rendering still works."""
    def _replacer(m):
        raw_tag = m.group(1)
        tag = raw_tag.lstrip('/').split()[0].lower()
        return '' if tag not in _KNOWN_HTML_TAGS else m.group(0)
    return re.sub(r'<(/?\s*[a-zA-Z][a-zA-Z0-9_:-]*(?:\s[^>]*)?)\s*/?>', _replacer, content)

def _render_markdown_with_mermaid(content: str, key_prefix: str = "md"):
    content = _strip_llm_xml(content)
    parts = re.split(r'```mermaid\s*\n(.*?)\n\s*```', content, flags=re.DOTALL)
    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part.strip():
                st.markdown(part, unsafe_allow_html=True)
        else:
            _render_mermaid(part, key=f"{key_prefix}_mermaid_{i}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _phase_counts(statuses: dict):
    auto_ids = [pid for pid, _, _, _, auto in PHASE_META if auto]
    done  = sum(1 for p in auto_ids if _phase_status(statuses, p) == "completed")
    total = len(auto_ids)
    pct   = int(done / total * 100) if total else 0
    return done, total, pct


def _step_state(pid: str, status: str, auto: bool) -> str:
    """Map phase status → CSS class for the stepper."""
    if not auto:
        return "manual"
    if status == "completed":
        return "done"
    if status in ("in_progress", "draft_pending"):
        return "active"
    return "pending"


# ── Topbar HTML ───────────────────────────────────────────────────────────────

def render_topbar(proj: dict | None = None, tab: str = "overview"):
    online = not settings.is_air_gapped
    engine_dot  = '<span class="pulse"></span>' if online else '<span style="width:6px;height:6px;border-radius:50%;background:var(--rose);display:inline-block;"></span>'
    engine_text = "ENGINE ONLINE" if online else "AIR-GAPPED"
    engine_color = "var(--emerald)" if online else "var(--rose)"

    proj_name = proj.get("name", "") if proj else ""
    proj_badge = (
        f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:10px;'
        f'color:var(--text3);letter-spacing:0.06em;border:1px solid var(--rim);'
        f'padding:3px 10px;margin-left:16px;">'
        f'▸ {proj_name}</span>'
    ) if proj_name else ""

    nav_items = [
        ("overview",    "OVERVIEW"),
        ("new",         "NEW PROJECT"),
        ("chat",        "DESIGN CHAT"),
        ("pipeline",    "PIPELINE"),
        ("docs",        "DOCUMENTS"),
        ("components",  "COMPONENTS"),
        ("netlist",     "NETLIST"),
        ("code",        "CODE REVIEW"),
        ("dashboard",   "DASHBOARD"),
    ]
    proj_id = st.session_state.get("project_id", "")
    proj_id_param = f"&project_id={proj_id}" if proj_id else ""
    nav_html = ""
    for key, label in nav_items:
        active_cls = "active" if tab == key else ""
        url = f"?tab={key}{proj_id_param}"
        nav_html += (
            f'<a href="{url}" target="_self" class="nav-item {active_cls}">'
            f'{label}</a>'
        )

    st.markdown(f"""
    <div class="topbar">
      <div class="logo-wrap">
        <div class="logo-mark">
          <svg width="26" height="26" viewBox="0 0 26 26" fill="none">
            <circle cx="13" cy="13" r="11" stroke="rgba(201,168,76,0.3)" stroke-width="1"/>
            <circle cx="13" cy="13" r="7"  stroke="rgba(201,168,76,0.5)" stroke-width="1"/>
          </svg>
          <div class="logo-inner"></div>
        </div>
        <div>
          <div class="logo-text">Hardware <em>Pipeline</em></div>
          <div class="logo-tag">AI Design Studio</div>
        </div>
      </div>
      {proj_badge}
      <div class="nav-center">{nav_html}</div>
      <div class="topbar-right">
        <div class="engine-status" style="color:{engine_color};">
          {engine_dot}
          <span>{engine_text}</span>
        </div>
        <div class="user-btn">{settings.primary_model[:2].upper()}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


# ── Sidebar (Left Panel — Pipeline Stepper) ────────────────────────────────────

def render_sidebar(statuses: dict | None = None, proj: dict | None = None):
    with st.sidebar:
        # Brand header
        st.markdown("""
        <div class="sidebar-brand">
          <div class="brand-icon">⚡</div>
          <div>
            <div class="brand-title">Pipeline</div>
            <div class="brand-sub">AI Hardware Design</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        if proj:
            done, total, pct = _phase_counts(statuses or {})
            dt_label = {"rf": "RF / Wireless", "digital": "Digital Logic"}.get(
                proj.get("design_type", "rf"), proj.get("design_type", "—"))
            dt_icon  = {"rf": "📡", "digital": "💻"}.get(proj.get("design_type", "rf"), "⚙️")

            st.markdown(f"""
            <div class="proj-card">
              <div class="proj-icon">{dt_icon}</div>
              <div>
                <div class="proj-name">{proj.get('name', '—')}</div>
                <div class="proj-type">{dt_label}</div>
              </div>
            </div>
            <div class="prog-bar">
              <div class="prog-fill" style="width:{pct}%"></div>
            </div>
            <div class="prog-label">{done}/{total} phases &nbsp;·&nbsp; {pct}%</div>
            """, unsafe_allow_html=True)

        # Section label + pipeline steps
        steps_html = '<div class="panel-label">Pipeline Stages</div>'
        steps_html += '<div class="panel-section">'

        # Build project URL param for step links
        _proj_id   = proj.get("id", "") if proj else ""
        _pid_param = f"&project_id={_proj_id}" if _proj_id else ""

        for idx, (pid, num, name, desc, auto) in enumerate(PHASE_META):
            status = _phase_status(statuses or {}, pid)
            if not auto:
                state = "manual"
            elif status == "completed":
                state = "done"
            elif status in ("in_progress", "draft_pending"):
                state = "active"
            else:
                state = "pending"

            badge_content = {"done": "✓", "active": num, "manual": num, "pending": num}.get(state, num)
            sub_text = {"done": "Complete", "active": "Running…", "manual": "Manual step", "pending": "Pending"}.get(state, "Pending")
            lock_icon = "" if auto else " 🔒"

            # Determine navigation target: P1 → chat, everything else → pipeline
            # Steps are clickable when a project is loaded
            if proj:
                target_tab = "chat" if pid == "P1" else "pipeline"
                _url = f"?tab={target_tab}{_pid_param}"
                step_tag_open  = (
                    f'<a href="{_url}" target="_self" class="step {state}">'
                )
                step_tag_close = '</a>'
            else:
                step_tag_open  = f'<div class="step {state}">'
                step_tag_close = '</div>'

            # Add connector line before each step (except first)
            if idx > 0:
                steps_html += '<div class="step-line"></div>'

            steps_html += f"""
            {step_tag_open}
              <div class="step-badge">{badge_content}</div>
              <div class="step-body">
                <div class="step-title">P{num} · {name}{lock_icon}</div>
                <div class="step-sub">{sub_text}</div>
              </div>
            {step_tag_close}"""

        steps_html += '</div>'
        st.markdown(steps_html, unsafe_allow_html=True)

        # Metrics grid (2×2)
        if proj:
            done, total, pct = _phase_counts(statuses or {})
            auto_ids = [p for p, *_, a in PHASE_META if a]
            in_prog  = sum(1 for p in auto_ids if _phase_status(statuses or {}, p) == "in_progress")
            failed   = sum(1 for p in auto_ids if _phase_status(statuses or {}, p) == "failed")
            st.markdown(f"""
            <div class="panel-label" style="margin-top:16px;">Metrics</div>
            <div class="metric-grid">
              <div class="metric-cell">
                <div class="metric-val">{pct}%</div>
                <div class="metric-lbl">Progress</div>
              </div>
              <div class="metric-cell">
                <div class="metric-val green">{done}</div>
                <div class="metric-lbl">Done</div>
              </div>
              <div class="metric-cell">
                <div class="metric-val cyan">{in_prog}</div>
                <div class="metric-lbl">Running</div>
              </div>
              <div class="metric-cell">
                <div class="metric-val" style="color:{'var(--rose)' if failed else 'var(--text3)'}">{failed}</div>
                <div class="metric-lbl">Failed</div>
              </div>
            </div>
            """, unsafe_allow_html=True)

        # System status at bottom
        st.markdown('<div class="panel-label" style="margin-top:20px;">System</div>',
                    unsafe_allow_html=True)
        online = not settings.is_air_gapped
        dot = "🟢" if online else "🔴"
        st.markdown(f"""
        <div class="sys-row">
          <span>{dot} {'Online' if online else 'Air-Gapped'}</span>
          <span class="sys-model">{settings.primary_model}</span>
        </div>
        """, unsafe_allow_html=True)

        for provider, (ok, _) in settings.get_api_key_status().items():
            cls  = "ok"  if ok else "off"
            icon = "✓"   if ok else "—"
            st.markdown(f'<span class="key-badge {cls}">{icon} {provider}</span>',
                        unsafe_allow_html=True)


# ── Hero ──────────────────────────────────────────────────────────────────────

def _render_hero(eyebrow: str, title: str, subtitle: str = ""):
    sub_html = f'<div class="hero-desc">{subtitle}</div>' if subtitle else ""
    st.markdown(f"""
    <div class="page-hero">
      <div class="hero-eyebrow">{eyebrow}</div>
      <h1 class="hero-title"><em>{title}</em></h1>
      {sub_html}
    </div>
    """, unsafe_allow_html=True)


# ── Timeline bar ──────────────────────────────────────────────────────────────

def _render_timeline(statuses: dict):
    # Phase short labels matching premium HTML: REQ, HRS, COMP, NET, PCB, GLR, FPGA, SRS, SDD, CODE
    phase_labels = ["REQ", "HRS", "COMP", "NET", "PCB", "GLR", "FPGA", "SRS", "SDD", "CODE"]
    done, total, pct = _phase_counts(statuses)
    phases_html = ""
    for i, (pid, num, name, desc, auto) in enumerate(PHASE_META):
        status = _phase_status(statuses, pid)
        if not auto:
            phase_cls = "manual"
        elif status == "completed":
            phase_cls = "done"
        elif status in ("in_progress", "draft_pending"):
            phase_cls = "active"
        else:
            phase_cls = ""
        short = phase_labels[i] if i < len(phase_labels) else f"P{num}"
        phases_html += f'<div class="tl-phase {phase_cls}" title="{name}">{short}</div>'

    st.markdown(f"""
    <div class="timeline-bar">
      <div class="timeline-header">
        <span class="tl-title">Pipeline Progress</span>
        <span class="tl-pct">{pct}%</span>
      </div>
      <div class="tl-track">
        <div class="tl-fill" style="width:{pct}%"></div>
      </div>
      <div class="tl-phases">{phases_html}</div>
    </div>
    """, unsafe_allow_html=True)


# ── KPI output cards ──────────────────────────────────────────────────────────

def _render_kpi_cards(statuses: dict, proj: dict):
    done, total, pct = _phase_counts(statuses)
    in_prog = sum(1 for pid, *_, auto in PHASE_META
                  if auto and _phase_status(statuses, pid) == "in_progress")
    failed  = sum(1 for pid, *_, auto in PHASE_META
                  if auto and _phase_status(statuses, pid) == "failed")

    st.markdown(f"""
    <div class="output-grid">
      <div class="output-card">
        <div class="oc-icon gold">📋</div>
        <div class="oc-label">Phases Complete</div>
        <div class="oc-val gold">{done}<span style="font-size:14px;color:var(--text3)">/{total}</span></div>
        <div class="oc-sub">{pct}% of pipeline done</div>
        <div class="oc-bar"><div class="oc-bar-fill gold" style="width:{pct}%"></div></div>
      </div>
      <div class="output-card">
        <div class="oc-icon green">✅</div>
        <div class="oc-label">Pipeline Progress</div>
        <div class="oc-val green">{pct}<span style="font-size:14px;color:var(--text3)">%</span></div>
        <div class="oc-sub">{done} auto phases complete</div>
        <div class="oc-bar"><div class="oc-bar-fill green" style="width:{pct}%"></div></div>
      </div>
      <div class="output-card">
        <div class="oc-icon {'cyan' if in_prog else 'rose'}">{'⚡' if in_prog else '⚠️'}</div>
        <div class="oc-label">{'Running Now' if in_prog else 'Failed'}</div>
        <div class="oc-val {'cyan' if in_prog else 'rose'}">{in_prog if in_prog else failed}</div>
        <div class="oc-sub">{'phases currently active' if in_prog else 'phases need attention'}</div>
        <div class="oc-bar"><div class="oc-bar-fill {'cyan' if in_prog else 'gold'}" style="width:{min(in_prog*25,100) if in_prog else (failed*20)}%"></div></div>
      </div>
    </div>
    """, unsafe_allow_html=True)


# ── SVG ring component ────────────────────────────────────────────────────────

def _svg_ring(value: int, label: str, color_cls: str, size: int = 72) -> str:
    """color_cls: 'gold' | 'green' | 'cyan'"""
    r    = (size - 10) / 2
    circ = 2 * 3.14159265 * r
    dash = circ * value / 100
    gap  = circ - dash
    cx = cy = size / 2
    # Map color class to actual color value for text fill
    color_map = {"gold": "var(--gold-light)", "green": "var(--emerald)", "cyan": "var(--cyan)"}
    color = color_map.get(color_cls, "var(--gold-light)")
    return f"""
    <div class="ring-wrap">
      <svg class="ring-svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
        <circle class="ring-track" cx="{cx}" cy="{cy}" r="{r}"/>
        <circle class="ring-fill {color_cls}" cx="{cx}" cy="{cy}" r="{r}"
                stroke-dasharray="{dash:.1f} {gap:.1f}"
                stroke-dashoffset="0"/>
        <text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="middle"
              class="ring-label" fill="{color}"
              font-family="'Cormorant Garamond',serif"
              font-size="{size//5}" font-weight="600"
              transform="rotate(90 {cx} {cy})">{value}%</text>
      </svg>
      <div class="ring-sub-lbl">{label}</div>
    </div>
    """


# ── Right panel ───────────────────────────────────────────────────────────────

def _render_right_panel(statuses: dict, proj: dict | None = None):
    done, total, pct = _phase_counts(statuses)
    auto_ids = [pid for pid, *_, auto in PHASE_META if auto]
    comp_done = sum(1 for p in auto_ids[:3]
                    if _phase_status(statuses, p) == "completed")
    comp_pct  = int(comp_done / 3 * 100) if auto_ids else 0
    hw_done   = sum(1 for p in auto_ids[3:5]
                    if _phase_status(statuses, p) == "completed")
    hw_pct    = int(hw_done / 2 * 100) if auto_ids else 0

    rings_html = (
        _svg_ring(pct,      "OVERALL", "gold") +
        _svg_ring(comp_pct, "DOCS",    "cyan") +
        _svg_ring(hw_pct,   "HW",      "green")
    )

    proj_name = proj.get("name", "No project loaded") if proj else "No project loaded"
    design_type = proj.get("design_type", "rf") if proj else "rf"

    # ── Industry compliance standards (adapted by design type) ──────────
    p3_status = _phase_status(statuses, "P3") if statuses else "pending"
    p1_status = _phase_status(statuses, "P1") if statuses else "pending"

    # RF: FCC, CE Mark, RoHS, REACH, AEC-Q100, ISO 26262
    # Digital: EMC/ESD, CE Mark, RoHS, REACH, AEC-Q100, ISO 26262
    if design_type == "rf":
        standards = [
            ("FCC",       "RF Emission"),
            ("CE Mark",   "EU Conformity"),
            ("RoHS",      "Hazardous Sub."),
            ("REACH",     "Chemical Safety"),
            ("AEC-Q100",  "Automotive IC"),
            ("ISO 26262", "Func. Safety"),
        ]
    else:
        standards = [
            ("EMC/ESD",   "EM Compat."),
            ("CE Mark",   "EU Conformity"),
            ("RoHS",      "Hazardous Sub."),
            ("REACH",     "Chemical Safety"),
            ("AEC-Q100",  "Automotive IC"),
            ("ISO 26262", "Func. Safety"),
        ]

    # Derive status: P3 complete → RoHS/REACH/CE pass, P1 → requirements checked
    comp_items = ""
    for std_name, std_desc in standards:
        if std_name in ("RoHS", "REACH", "CE Mark") and p3_status == "completed":
            cls, dot = "pass", "✓"
        elif std_name in ("FCC", "EMC/ESD") and p3_status == "completed":
            cls, dot = "pass", "✓"
        elif std_name in ("AEC-Q100",) and p3_status == "completed":
            cls, dot = "warn", "◉"  # needs further validation
        elif p3_status in ("in_progress", "draft_pending"):
            cls, dot = "warn", "◉"
        elif p3_status == "failed":
            cls, dot = "fail", "✕"
        else:
            cls, dot = "pending-c", "○"
        comp_items += f"""
        <div class="comp-check {cls}">
          <span class="comp-check-icon">{dot}</span>
          <span class="comp-check-name">{std_name}</span>
        </div>"""

    # ── AI Activity feed — last 5 events ────────────────────────────────
    activity = []
    for pid, num, name, _, auto in PHASE_META:
        s = _phase_status(statuses, pid)
        if s == "completed":
            activity.append(("green", f"P{num} {name} complete", "done"))
        elif s == "in_progress":
            activity.append(("gold", f"P{num} {name} running…", "active"))
        elif s == "failed":
            activity.append(("amber", f"P{num} {name} failed", "fail"))
    activity = activity[-5:] if activity else []

    act_items = ""
    time_labels = ["just now", "2m ago", "5m ago", "12m ago", "30m ago"]
    for i, (dot_cls, msg, _) in enumerate(reversed(activity)):
        t = time_labels[i] if i < len(time_labels) else "earlier"
        act_items += f"""
        <div class="activity-item">
          <div class="act-dot {dot_cls}"></div>
          <div class="act-content">
            <div class="act-msg">{msg}</div>
            <div class="act-time">{t}</div>
          </div>
        </div>"""

    if not act_items:
        act_items = """
        <div class="activity-item">
          <div class="act-dot dim"></div>
          <div class="act-content">
            <div class="act-msg" style="color:var(--text4)">No activity yet</div>
          </div>
        </div>"""

    st.markdown(f"""
    <div class="right-panel">

      <div class="checkpoint-card">
        <div class="cp-header">
          <span class="cp-icon">⚡</span>
          ACTIVE PROJECT
        </div>
        <div class="cp-msg">{proj_name}<br>
          <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--text4)">
            {done} / {total} phases complete
          </span>
        </div>
        <div style="height:2px;background:var(--rim);margin-bottom:8px;">
          <div style="height:100%;width:{pct}%;background:linear-gradient(90deg,var(--gold),var(--gold-light));"></div>
        </div>
        <div class="cp-btns">
          <button class="cp-btn-approve" onclick="">APPROVE</button>
          <button class="cp-btn-revise" onclick="">REVISE</button>
        </div>
      </div>

      <div class="rp-section">
        <div class="rp-section-label">Industry Compliance</div>
        <div class="compliance-grid">{comp_items}</div>
      </div>

      <div class="gold-divider"></div>

      <div class="rp-section">
        <div class="rp-section-label">Quality Rings</div>
        <div class="kpi-rings">{rings_html}</div>
      </div>

      <div class="gold-divider"></div>

      <div class="rp-section">
        <div class="rp-section-label">AI Activity Feed</div>
        <div class="activity-feed">{act_items}</div>
      </div>

    </div>
    """, unsafe_allow_html=True)


# ── Overview ───────────────────────────────────────────────────────────────────

def render_overview():
    _render_hero(
        "AI-Powered Hardware Design",
        "Pipeline <em>Studio</em>",
        "IEEE-compliant · RF & Digital · Air-Gap Ready"
    )

    statuses = {}
    if "project_id" in st.session_state:
        statuses = _load_status(st.session_state.project_id)
        proj_data = _load_project(st.session_state.project_id) or {}
        _render_kpi_cards(statuses, proj_data)

    st.markdown("""
    <div class="output-grid" style="margin-bottom:24px;">
      <div class="output-card">
        <div class="oc-icon gold">📋</div>
        <div class="oc-label">IEEE Standards</div>
        <div class="oc-val gold" style="font-size:18px;margin-bottom:8px;">HRS · SRS · SDD</div>
        <div class="oc-sub">29148, 830, 1016 — audit-ready, fully traceable</div>
      </div>
      <div class="output-card">
        <div class="oc-icon cyan">🔌</div>
        <div class="oc-label">Smart Netlist</div>
        <div class="oc-val cyan" style="font-size:18px;margin-bottom:8px;">Visual DRC</div>
        <div class="oc-sub">Connectivity graph before PCB layout</div>
      </div>
      <div class="output-card">
        <div class="oc-icon green">✅</div>
        <div class="oc-label">Compliance Engine</div>
        <div class="oc-val green" style="font-size:18px;margin-bottom:8px;">RoHS · FCC</div>
        <div class="oc-sub">REACH / MIL-STD rules — PASS / FAIL / REVIEW</div>
      </div>
    </div>
    <div class="section-card" style="margin-bottom:24px;">
      <div class="card-header">
        <div class="card-title">
          💻 Code Generation
          <span class="live-badge"><span class="pulse"></span>AUTO</span>
        </div>
      </div>
      <div style="padding:16px 24px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--text3);line-height:1.7;">
        C/C++ drivers + test suites, reviewed with AST analysis — Phase 8c
      </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("➕ Start New Project", use_container_width=True, type="primary"):
            st.query_params["tab"] = "new"; st.rerun()
    with col2:
        if st.button("📊 View Dashboard", use_container_width=True):
            st.query_params["tab"] = "dashboard"; st.rerun()
    with col3:
        if st.button("📄 Browse Documents", use_container_width=True):
            st.query_params["tab"] = "docs"; st.rerun()


# ── New Project ────────────────────────────────────────────────────────────────

def render_new_project():
    _render_hero("Create", "New <em>Project</em>", "Set up your hardware design workspace")

    # Load existing projects
    existing = _api_get("/api/v1/projects") or []
    if existing:
        st.markdown("""
        <div class="section-card">
          <div class="card-header">
            <div class="card-title">📂 Load Existing Project</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        proj_names = {p["id"]: f"{p['name']}  ({p.get('design_type','—')})" for p in existing}
        selected = st.selectbox("Select a project", options=list(proj_names.keys()),
                                format_func=lambda x: proj_names[x], key="load_existing")
        if st.button("📂 Load Project", use_container_width=True):
            st.session_state.project_id = selected
            st.session_state.current_project = next(p for p in existing if p["id"] == selected)
            st.query_params["project_id"] = str(selected)
            st.query_params["tab"] = "chat"
            # DON'T call _reset_chat() — let render_design_chat() load history from DB
            st.session_state.pop("chat_messages", None)
            st.session_state.pop("draft_pending", None)
            st.session_state.pop("_phase_overrides", None)
            st.rerun()
        st.markdown("<hr class='hp-divider'>", unsafe_allow_html=True)

    st.markdown("""
    <div class="section-card">
      <div class="card-header">
        <div class="card-title">✨ Create New Project</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    with st.form("new_project_form", clear_on_submit=False):
        col1, col2 = st.columns([2, 1])
        with col1:
            name = st.text_input("Project Name *", placeholder="e.g., RF Transceiver 2.4GHz")
            description = st.text_area("Description",
                                       placeholder="Brief description of the hardware design…",
                                       height=110)
        with col2:
            design_type = st.selectbox("Design Type *",
                ["rf", "digital"],
                format_func=lambda x: {
                    "rf":      "📡 RF / Wireless",
                    "digital": "💻 Digital Logic",
                }.get(x, x))
            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button("🚀 Create & Start", type="primary",
                                              use_container_width=True)

    if submitted:
        if not name.strip():
            st.markdown('<div class="hp-alert warn">⚠️ Project name is required.</div>',
                        unsafe_allow_html=True)
            return
        if len(name.strip()) > 100:
            st.markdown('<div class="hp-alert warn">⚠️ Project name too long (max 100 characters).</div>',
                        unsafe_allow_html=True)
            return
        if not re.search(r'[a-zA-Z0-9]', name):
            st.markdown('<div class="hp-alert warn">⚠️ Project name must contain at least one letter or number.</div>',
                        unsafe_allow_html=True)
            return
        existing_names = [p.get("name", "").strip().lower() for p in (_api_get("/api/v1/projects") or [])]
        if name.strip().lower() in existing_names:
            st.markdown('<div class="hp-alert warn">⚠️ A project with this name already exists.</div>',
                        unsafe_allow_html=True)
            return
        with st.spinner("Creating project…"):
            result = _api_post("/api/v1/projects",
                               {"name": name, "description": description,
                                "design_type": design_type})
            if result is None:
                try:
                    from services.project_service import ProjectService
                    result = ProjectService().create(name, description, design_type)
                except Exception as exc:
                    st.error(f"Failed to create project: {exc}")
                    return

            if not result or "id" not in result:
                st.markdown('<div class="hp-alert warn">⚠️ Failed to create project. Check if the API server is running.</div>',
                            unsafe_allow_html=True)
                return

            st.session_state.current_project = result
            st.session_state.project_id = result["id"]
            st.query_params["project_id"] = str(result["id"])
            _reset_chat()
            st.markdown(f'<div class="hp-alert success">✅ Project <strong>{name}</strong> created!</div>',
                        unsafe_allow_html=True)
            log.info("ui.new_project", extra={"project_id": result["id"]})
            time.sleep(0.5)
            st.query_params["tab"] = "chat"
            st.rerun()


# ── Design Chat (Phase 1) ──────────────────────────────────────────────────────

def _reset_chat():
    st.session_state.chat_messages = [{
        "role": "assistant",
        "content": (
            "👋 **Welcome to Silicon to Software (S2S)!**\n\n"
            "Tell me what you want to design — I'll instantly generate a **draft block diagram** "
            "for you to review. No long questionnaires.\n\n"
            "**Examples:**\n"
            "- *3-phase BLDC motor controller, 10kW, 48V bus*\n"
            "- *RF amplifier, 40dBm output, 2.4GHz*\n"
            "- *48V → 3.3V/5V/12V power supply, 200W total*\n\n"
            "Just describe your design and I'll produce a draft in seconds. ⚡"
        ),
    }]
    st.session_state.draft_pending = False
    st.session_state.phase1_complete = False
    # Clear phase overrides so sidebar reflects actual DB state
    st.session_state.pop("_phase_overrides", None)


def render_design_chat():
    _render_hero("Phase 1", "Design <em>Chat</em>", "AI requirements capture — describe your hardware")

    if "project_id" not in st.session_state:
        st.markdown('<div class="hp-alert info">Create a project first in <strong>New Project</strong>.</div>',
                    unsafe_allow_html=True)
        if st.button("➕ Create Project", type="primary"):
            st.query_params["tab"] = "new"; st.rerun()
        return

    proj_id = st.session_state.project_id
    statuses = _load_status(proj_id)
    phase1_status = _phase_status(statuses, "P1")
    proj = _load_project(proj_id) or st.session_state.get("current_project", {})

    dt_icons = {"rf": "📡", "digital": "💻"}
    dt = proj.get("design_type", "rf")
    st.markdown(
        f'<div class="hp-proj-tag">'
        f'{dt_icons.get(dt, "⚙️")} <strong>{proj.get("name", "")}</strong>'
        f' <span class="hp-tag">{dt}</span></div>',
        unsafe_allow_html=True)

    # Phase 1 complete banner
    if phase1_status == "completed":
        st.markdown('<div class="hp-alert success">✅ <strong>Phase 1 Complete!</strong> '
                    'Requirements and block diagrams generated. Ready to run the pipeline.</div>',
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("🚀 Run Pipeline", use_container_width=True,
                         key="btn_run_pipeline"):
                _start_pipeline(proj_id)
        with c2:
            if st.button("📄 View Docs", use_container_width=True, key="btn_docs"):
                st.query_params["tab"] = "docs"; st.rerun()
        with c3:
            if st.button("🔄 New Chat", use_container_width=True, key="btn_new_chat"):
                _reset_chat(); st.rerun()
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    if "chat_messages" not in st.session_state:
        # Try to restore chat history from DB before falling back to welcome message
        proj_data = _load_project(proj_id)
        history = (proj_data or {}).get("conversation_history", [])
        if history:
            st.session_state.chat_messages = [
                {"role": m["role"], "content": m["content"]}
                for m in history
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
        else:
            _reset_chat()

    # Chat messages with premium bubbles
    for idx, msg in enumerate(st.session_state.chat_messages):
        role_cls = "user" if msg["role"] == "user" else "ai"
        avatar   = "👤" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            _render_markdown_with_mermaid(msg["content"], key_prefix=f"chat_{idx}")

    # Input area
    if phase1_status != "completed":
        if st.session_state.get("draft_pending"):
            st.markdown("""
            <div class="hp-alert info">📋 <strong>Draft ready for review.</strong>
            Approve to generate full IEEE documentation, or request changes below.</div>
            """, unsafe_allow_html=True)
            col_a, col_b = st.columns([1, 2])
            with col_a:
                if st.button("✅ Approve — Generate Full Docs", type="primary",
                             use_container_width=True, key="btn_approve"):
                    st.session_state["_pending_chat"] = "Approved. Please generate the full requirements documents."
            with col_b:
                change_text = st.text_input("Request changes…", key="change_input",
                                            placeholder="e.g., change voltage to 24V, add CAN bus")
                if st.button("🔄 Apply Changes", use_container_width=True, key="btn_changes"):
                    if change_text.strip():
                        st.session_state["_pending_chat"] = change_text.strip()
                        st.session_state["change_input"] = ""
                    else:
                        st.markdown('<div class="hp-alert warn">⚠️ Please enter your requested changes.</div>',
                                    unsafe_allow_html=True)
        else:
            if user_input := st.chat_input("Describe your hardware design…"):
                st.session_state["_pending_chat"] = user_input

    # Process pending chat OUTSIDE any column context so messages render full-width
    if "_pending_chat" in st.session_state:
        pending = st.session_state.pop("_pending_chat")
        _send_chat(pending)


def _send_chat(user_input: str):
    proj_id = st.session_state.project_id
    st.session_state.chat_messages.append({"role": "user", "content": user_input})

    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)

    with st.chat_message("assistant", avatar="🤖"):
        placeholder = st.empty()
        is_approval = any(kw in user_input.lower()
                          for kw in ("approve", "yes", "ok", "good", "proceed", "go ahead"))
        action = "Generating full requirements documentation…" if is_approval \
                 else "Generating draft block diagram…"
        placeholder.markdown(
            f'<div class="hp-proc"><div class="hp-spinner"></div>{action}</div>',
            unsafe_allow_html=True)

        t0 = time.time()
        result = _api_post(f"/api/v1/projects/{proj_id}/chat",
                           {"message": user_input}, timeout=300.0)

        if result is None:
            try:
                from services.chat_service import ChatService
                import asyncio
                result = asyncio.run(ChatService().send_message(proj_id, user_input))
            except Exception as exc:
                placeholder.empty()
                st.error(f"Error: {exc}")
                return

        elapsed = time.time() - t0
        placeholder.empty()

        response = result.get("response", "")
        _render_markdown_with_mermaid(response, key_prefix="resp")
        st.caption(f"⏱️ Generated in {elapsed:.1f}s")
        st.session_state.chat_messages.append({"role": "assistant", "content": response})

        if result.get("draft_pending"):
            st.session_state.draft_pending = True
            # Belt-and-suspenders: also cache in overrides so sidebar updates immediately
            if "_phase_overrides" not in st.session_state:
                st.session_state["_phase_overrides"] = {}
            st.session_state["_phase_overrides"]["P1"] = "draft_pending"
            st.rerun()

        if result.get("phase_complete"):
            st.session_state.draft_pending = False
            # Cache completion in session state so sidebar/status reflect it immediately
            # (guards against async DB write not yet visible to sync status read)
            if "_phase_overrides" not in st.session_state:
                st.session_state["_phase_overrides"] = {}
            st.session_state["_phase_overrides"]["P1"] = "completed"
            st.balloons()
            st.markdown(
                f'<div class="hp-alert success">🎉 <strong>Phase 1 Complete!</strong> '
                f'Generated in {elapsed:.1f}s. Full documentation ready.</div>',
                unsafe_allow_html=True)
            if result.get("outputs"):
                with st.expander("📁 Generated Files", expanded=True):
                    for fname in result["outputs"]:
                        st.markdown(f'<span class="hp-tag">📄 {fname}</span>', unsafe_allow_html=True)
            # Rerun so the persistent Phase 1 Complete banner at the top of
            # render_design_chat() takes over with stable buttons.
            st.rerun()


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _start_pipeline(project_id: int):
    result = _api_post(f"/api/v1/projects/{project_id}/pipeline/run", {})
    if result:
        st.query_params["tab"] = "pipeline"
        st.rerun()
    else:
        st.error("Could not start pipeline — is the API server running?")


def _phase_card_html(pid, num, name, desc, status, statuses, auto):
    """Render a premium phase card."""
    state_cls = {
        "completed":   "done",
        "in_progress": "active",
        "draft_pending": "active",
        "failed":      "fail",
    }.get(status, "")

    badge_cls = {
        "completed":   "hp-badge-done",
        "in_progress": "hp-badge-run",
        "failed":      "hp-badge-fail",
    }.get(status, "hp-badge-pend")

    status_label = {
        "pending":      "Pending",
        "in_progress":  "Running…",
        "completed":    "✓ Complete",
        "failed":       "✕ Failed",
        "draft_pending":"Draft Ready",
    }.get(status, status.title())

    phase_icons = {
        "P1": "🎨", "P2": "📋", "P3": "✅", "P4": "🔌",
        "P5": "📐", "P6": "⚙️", "P7": "💎", "P8a": "📄",
        "P8b": "📘", "P8c": "💻",
    }
    icon_display = phase_icons.get(pid, "📦")

    status_icon = {"completed": "✓", "in_progress": "◉", "failed": "✕",
                   "draft_pending": "◑"}.get(status, num)

    # Render using simple st.markdown + st.columns to avoid Streamlit HTML truncation
    status_emoji = {"completed": "✅", "in_progress": "🔄", "failed": "❌",
                    "draft_pending": "📋"}.get(status, "⏳")

    st.markdown("---")
    col_icon, col_meta, col_status = st.columns([1, 6, 2])
    with col_icon:
        st.markdown(f"### {status_icon}")
    with col_meta:
        st.markdown(f"**{icon_display} P{num} · {name}**")
        st.caption(desc)
    with col_status:
        st.markdown(f"{status_emoji} **{status_label}**")
        if not auto:
            st.caption("🔒 Manual")

    err = statuses.get(pid, {}).get("error", "")
    if err and status == "failed":
        st.error(f"⚠️ {err}")
    return ""  # No HTML to render — already rendered above


def render_pipeline():
    if "project_id" not in st.session_state:
        _render_hero("Pipeline", "No Project Loaded")
        st.markdown('<div class="hp-alert info">Create a project first.</div>', unsafe_allow_html=True)
        return

    proj_id  = st.session_state.project_id
    statuses = _load_status(proj_id)
    proj     = _load_project(proj_id) or st.session_state.get("current_project", {})
    done, total, pct = _phase_counts(statuses)

    _render_hero(
        f"Project · {proj.get('design_type','rf').upper()}",
        f"<em>{proj.get('name', 'Pipeline')}</em>",
        "Phase-by-phase automated design generation"
    )

    _render_timeline(statuses)
    _render_kpi_cards(statuses, proj)

    # Action banner
    auto_ids   = [pid for pid, *_, auto in PHASE_META if auto]
    p1_status  = _phase_status(statuses, "P1")
    in_prog    = [p for p in auto_ids if _phase_status(statuses, p) == "in_progress"]
    fail_list  = [p for p in auto_ids if _phase_status(statuses, p) == "failed"]
    remaining  = [p for p in auto_ids if p != "P1" and _phase_status(statuses, p) != "completed"]

    if not remaining and p1_status == "completed":
        st.markdown('<div class="hp-alert success">🎉 <strong>All phases complete!</strong> Full design package ready.</div>',
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("📄 View Documents", type="primary", use_container_width=True):
                st.query_params["tab"] = "docs"; st.rerun()
        with c2:
            if st.button("🔌 View Netlist", use_container_width=True):
                st.query_params["tab"] = "netlist"; st.rerun()
        with c3:
            if st.button("🔍 Code Review", use_container_width=True):
                st.query_params["tab"] = "code"; st.rerun()

    elif p1_status not in ("completed", "draft_pending"):
        st.markdown('<div class="hp-alert warn">⚠️ <strong>Complete Phase 1</strong> (Design Chat) first.</div>',
                    unsafe_allow_html=True)
        if st.button("💬 Go to Design Chat", type="primary"):
            st.query_params["tab"] = "chat"; st.rerun()

    elif p1_status == "draft_pending":
        st.markdown('<div class="hp-alert info">📋 <strong>Draft Ready</strong> — approve your design in the Design Chat.</div>',
                    unsafe_allow_html=True)
        if st.button("💬 Go to Design Chat", type="primary"):
            st.query_params["tab"] = "chat"; st.rerun()

    elif in_prog:
        st.markdown(f'<div class="hp-alert info">🔄 Pipeline running… <strong>{", ".join(in_prog)}</strong></div>',
                    unsafe_allow_html=True)
    else:
        col_l, col_r = st.columns([3, 1])
        with col_r:
            if st.button("🚀 Run Pipeline", type="primary", use_container_width=True):
                _start_pipeline(proj_id)

    # Phase groups
    groups = [
        ("🎨 Design &amp; Requirements",       PHASE_META[0:1]),
        ("📋 Documentation &amp; Compliance",   PHASE_META[1:3]),
        ("🔌 Hardware Design",                 PHASE_META[3:5]),
        ("⚙️ Logic &amp; FPGA",                PHASE_META[5:7]),
        ("💻 Software &amp; Code",             PHASE_META[7:10]),
    ]

    for group_label, phases in groups:
        st.markdown(f'<div class="hp-group-label">{group_label}</div>', unsafe_allow_html=True)
        for pid, num, name, desc, auto in phases:
            status = _phase_status(statuses, pid)
            _phase_card_html(pid, num, name, desc, status, statuses, auto)
            if auto and pid != "P1" and p1_status == "completed":
                if status in ("pending", "failed"):
                    if st.button(f"▶ Run P{num}", key=f"run_{pid}"):
                        result = _api_post(f"/api/v1/projects/{proj_id}/phases/{pid}/execute", {})
                        if result:
                            st.rerun()
                elif status == "completed":
                    if st.button(f"📄 Output P{num}", key=f"view_{pid}"):
                        st.query_params["tab"] = "docs"; st.rerun()

    # Auto-refresh while running
    if in_prog:
        st.markdown('<div class="hp-proc"><div class="hp-spinner"></div>Pipeline running… auto-refresh in 3s</div>',
                    unsafe_allow_html=True)
        col_ref, _ = st.columns([1, 5])
        with col_ref:
            if st.button("🔄 Refresh Now", key="pipeline_refresh"):
                st.rerun()
        time.sleep(3)
        st.rerun()


# ── Documents ──────────────────────────────────────────────────────────────────

def render_documents():
    _render_hero("Generated", "<em>Documents</em>", "IEEE-compliant design documentation")

    if "project_id" not in st.session_state:
        st.markdown('<div class="hp-alert info">No project loaded.</div>', unsafe_allow_html=True)
        return

    proj = _load_project(st.session_state.project_id) \
           or st.session_state.get("current_project", {})
    output_dir = Path(proj.get("output_dir", ""))

    if not output_dir.exists():
        st.markdown('<div class="hp-alert info">No documents yet — complete Phase 1 in Design Chat.</div>',
                    unsafe_allow_html=True)
        return

    proj_name = proj.get("name", "project").replace(" ", "_").lower()
    doc_map = {
        "requirements.md":              ("P1",  "🎨 Hardware Requirements"),
        "block_diagram.md":             ("P1",  "🎨 Block Diagram"),
        "architecture.md":              ("P1",  "🎨 System Architecture"),
        "component_recommendations.md": ("P1",  "🎨 Component Recommendations"),
        f"HRS_{proj_name}.md":          ("P2",  "📋 HRS — Hardware Requirements Spec"),
        "compliance_report.md":         ("P3",  "✅ Compliance Report"),
        "netlist_visual.md":            ("P4",  "🔌 Netlist Visualization"),
        "glr_specification.md":         ("P6",  "⚙️ GLR — Glue Logic Requirements"),
        f"SRS_{proj_name}.md":          ("P8a", "📄 SRS — Software Requirements Spec"),
        f"SDD_{proj_name}.md":          ("P8b", "📘 SDD — Software Design Description"),
        "driver_code.md":               ("P8c", "💻 Driver Code"),
        "code_review.md":               ("P8c", "💻 Code Review"),
    }

    found_any = False
    for fname, (phase, label) in doc_map.items():
        fpath = output_dir / fname
        if fpath.exists():
            found_any = True
            content = fpath.read_text(encoding="utf-8")
            col_card, col_dl = st.columns([6, 1])
            with col_card:
                st.markdown(f"""
                <div class="hp-doc-card">
                  <div class="hp-doc-icon">📄</div>
                  <div class="hp-doc-meta">
                    <div class="hp-doc-name">{label}</div>
                    <div class="hp-doc-phase">{phase} · {fname}</div>
                  </div>
                  <span class="hp-tag">{phase}</span>
                </div>
                """, unsafe_allow_html=True)
            with col_dl:
                st.download_button(
                    "⬇ .md",
                    data=content,
                    file_name=fname,
                    mime="text/markdown",
                    key=f"dl_{fname}",
                    use_container_width=True,
                )
            with st.expander(f"View {label}"):
                _render_markdown_with_mermaid(content, key_prefix=f"doc_{fname}")

    # Also list any C/H source files generated
    src_dir = output_dir / "src"
    if src_dir.exists():
        for src_file in sorted(src_dir.glob("*")):
            if src_file.suffix in (".c", ".h", ".cpp"):
                found_any = True
                content = src_file.read_text(encoding="utf-8", errors="replace")
                col_card, col_dl = st.columns([6, 1])
                with col_card:
                    st.markdown(f"""
                    <div class="hp-doc-card">
                      <div class="hp-doc-icon">💻</div>
                      <div class="hp-doc-meta">
                        <div class="hp-doc-name">P8c · {src_file.name}</div>
                        <div class="hp-doc-phase">P8c · src/{src_file.name}</div>
                      </div>
                      <span class="hp-tag">P8c</span>
                    </div>
                    """, unsafe_allow_html=True)
                with col_dl:
                    st.download_button(
                        f"⬇ {src_file.suffix}",
                        data=content,
                        file_name=src_file.name,
                        mime="text/plain",
                        key=f"dl_docs_src_{src_file.name}",
                        use_container_width=True,
                    )
                with st.expander(f"View {src_file.name}"):
                    st.code(content, language="c")

    if not found_any:
        st.markdown('<div class="hp-alert info">No documents generated yet.</div>',
                    unsafe_allow_html=True)


# ── Netlist ────────────────────────────────────────────────────────────────────

def render_netlist():
    _render_hero("Phase 4", "Netlist <em>Visualization</em>", "Component connectivity graph with DRC checks")

    if "project_id" not in st.session_state:
        st.markdown('<div class="hp-alert info">No project loaded.</div>', unsafe_allow_html=True)
        return

    proj = _load_project(st.session_state.project_id) or {}
    output_dir = Path(proj.get("output_dir", ""))
    netlist_visual = output_dir / "netlist_visual.md"
    netlist_json   = output_dir / "netlist.json"

    found = False

    if netlist_visual.exists():
        found = True
        content = netlist_visual.read_text(encoding="utf-8")
        st.markdown("""
        <div class="section-card">
          <div class="card-header">
            <div class="card-title">🔌 Netlist Diagram
              <span class="live-badge"><span class="pulse"></span>VISUAL</span>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        _render_markdown_with_mermaid(content, key_prefix="netlist_vis")
        col_dl, _ = st.columns([1, 5])
        with col_dl:
            st.download_button("⬇ netlist_visual.md", content,
                               file_name="netlist_visual.md", mime="text/markdown",
                               key="dl_netlist_vis")

    if netlist_json.exists():
        found = True
        raw = netlist_json.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
            nodes = len(data.get("nodes", data.get("components", [])))
            edges = len(data.get("edges", data.get("connections", data.get("nets", []))))
        except Exception:
            nodes = edges = "?"
        st.markdown(f"""
        <div class="section-card" style="margin-top:16px;">
          <div class="card-header">
            <div class="card-title">📊 Netlist Data</div>
            <span class="hp-tag">{nodes} nodes · {edges} nets</span>
          </div>
        </div>
        """, unsafe_allow_html=True)
        with st.expander("View netlist.json"):
            st.json(json.loads(raw))
        col_dl, _ = st.columns([1, 5])
        with col_dl:
            st.download_button("⬇ netlist.json", raw,
                               file_name="netlist.json", mime="application/json",
                               key="dl_netlist_json")

    if not found:
        statuses = _load_status(st.session_state.project_id)
        p4_status = _phase_status(statuses, "P4")
        if p4_status == "in_progress":
            st.markdown('<div class="hp-alert info">🔄 Netlist generation in progress… refresh to check.</div>',
                        unsafe_allow_html=True)
        elif p4_status == "failed":
            err = statuses.get("P4", {}).get("error", "Unknown error")
            st.markdown(f'<div class="hp-alert warn">⚠️ Netlist generation failed: {err}</div>',
                        unsafe_allow_html=True)
            st.markdown('<div class="hp-alert info">Go to the <strong>Pipeline</strong> tab and click <strong>▶ Run P4</strong> to retry.</div>',
                        unsafe_allow_html=True)
            if st.button("🔄 Go to Pipeline", type="primary"):
                st.query_params["tab"] = "pipeline"
                st.rerun()
        elif p4_status == "completed":
            st.markdown('<div class="hp-alert warn">⚠️ Phase 4 marked complete but no netlist output found. Output files may have been moved or deleted.</div>',
                        unsafe_allow_html=True)
        else:
            # pending — P4 hasn't been run yet
            st.markdown('<div class="hp-alert info">📋 Phase 4 (Netlist Generation) has not run yet. Go to the <strong>Pipeline</strong> tab and click <strong>▶ Run P4</strong> to generate the netlist.</div>',
                        unsafe_allow_html=True)
            if st.button("📐 Go to Pipeline", type="primary"):
                st.query_params["tab"] = "pipeline"
                st.rerun()


# ── Code Review ────────────────────────────────────────────────────────────────

def render_code_review():
    _render_hero("Phase 8c", "Code <em>Review</em>", "Generated C/C++ drivers, test suites, AST review")

    if "project_id" not in st.session_state:
        st.markdown('<div class="hp-alert info">No project loaded.</div>', unsafe_allow_html=True)
        return

    proj = _load_project(st.session_state.project_id) or {}
    output_dir = Path(proj.get("output_dir", ""))
    found = False

    # Markdown reports
    for fname, label, icon in [
        ("code_review_report.md", "Code Review Report", "🔍"),
        ("driver_code.md",        "Driver Code (md)",   "💻"),
        ("code_review.md",        "Code Review",        "🔍"),
    ]:
        fpath = output_dir / fname
        if fpath.exists():
            found = True
            content = fpath.read_text(encoding="utf-8")
            col_h, col_dl = st.columns([6, 1])
            with col_h:
                st.markdown(f"""
                <div class="hp-doc-card">
                  <div class="hp-doc-icon">{icon}</div>
                  <div class="hp-doc-meta">
                    <div class="hp-doc-name">{label}</div>
                    <div class="hp-doc-phase">P8c · {fname}</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)
            with col_dl:
                st.download_button("⬇ .md", content, file_name=fname,
                                   mime="text/markdown", key=f"dl_cr_{fname}",
                                   use_container_width=True)
            with st.expander(f"{icon} {label}", expanded=(fname == "code_review_report.md")):
                _render_markdown_with_mermaid(content, key_prefix=f"code_{fname}")

    # C/H source files in src/
    src_dir = output_dir / "src"
    if src_dir.exists():
        c_files = sorted(src_dir.glob("*.c")) + sorted(src_dir.glob("*.h")) + \
                  sorted(src_dir.glob("*.cpp")) + sorted(src_dir.glob("*.hpp"))
        if c_files:
            st.markdown("""
            <div class="hp-group-label">Generated Source Files</div>
            """, unsafe_allow_html=True)
            for sf in c_files:
                found = True
                code = sf.read_text(encoding="utf-8", errors="replace")
                col_h, col_dl = st.columns([6, 1])
                with col_h:
                    st.markdown(f"""
                    <div class="hp-doc-card">
                      <div class="hp-doc-icon">💻</div>
                      <div class="hp-doc-meta">
                        <div class="hp-doc-name">{sf.name}</div>
                        <div class="hp-doc-phase">P8c · src/{sf.name}</div>
                      </div>
                    </div>
                    """, unsafe_allow_html=True)
                with col_dl:
                    st.download_button(f"⬇ {sf.suffix}", code, file_name=sf.name,
                                       mime="text/plain", key=f"dl_code_src_{sf.name}",
                                       use_container_width=True)
                with st.expander(f"💻 {sf.name}"):
                    st.code(code, language="c")

    # Makefile
    makefile = output_dir / "Makefile"
    if makefile.exists():
        found = True
        mk_content = makefile.read_text(encoding="utf-8", errors="replace")
        with st.expander("🔧 Makefile"):
            st.code(mk_content, language="makefile")

    if not found:
        st.markdown('<div class="hp-alert info">Run the pipeline to generate code (Phase 8c).</div>',
                    unsafe_allow_html=True)
        if st.button("🚀 Run Pipeline", type="primary"):
            _start_pipeline(st.session_state.project_id)


# ── Components ─────────────────────────────────────────────────────────────────

def render_components():
    _render_hero("Phase 1 · P3", "<em>Components</em>", "BOM recommendations, compliance grades, availability status")

    if "project_id" not in st.session_state:
        st.markdown('<div class="hp-alert info">No project loaded.</div>', unsafe_allow_html=True)
        return

    proj = _load_project(st.session_state.project_id) or {}
    output_dir = Path(proj.get("output_dir", ""))

    # ── Try components.json first (structured BOM) ─────────────────────────────
    comp_json = output_dir / "components.json"
    comp_md   = output_dir / "component_recommendations.md"

    if comp_json.exists():
        try:
            data = json.loads(comp_json.read_text(encoding="utf-8"))
            components = data if isinstance(data, list) else data.get("components", [])
        except Exception:
            components = []
    else:
        components = []

    # ── Summary cards ──────────────────────────────────────────────────────────
    total     = len(components)
    active    = sum(1 for c in components if str(c.get("status","")).lower() in ("active","ok","verified"))
    flagged   = sum(1 for c in components if "flag" in str(c.get("status","")).lower()
                                              or "review" in str(c.get("status","")).lower())
    alts      = sum(1 for c in components if "alt" in str(c.get("status","")).lower())

    if total:
        st.markdown(f"""
        <div class="output-grid" style="margin-bottom:24px;">
          <div class="output-card">
            <div class="oc-icon gold">🧩</div>
            <div class="oc-label">Total Components</div>
            <div class="oc-val gold">{total}</div>
            <div class="oc-sub">From BOM</div>
            <div class="oc-bar"><div class="oc-bar-fill gold" style="width:100%"></div></div>
          </div>
          <div class="output-card">
            <div class="oc-icon green">✓</div>
            <div class="oc-label">Active / Verified</div>
            <div class="oc-val green">{active}</div>
            <div class="oc-sub">In production</div>
            <div class="oc-bar"><div class="oc-bar-fill green" style="width:{int(active/total*100) if total else 0}%"></div></div>
          </div>
          <div class="output-card">
            <div class="oc-icon cyan">⚡</div>
            <div class="oc-label">Alternatives</div>
            <div class="oc-val cyan">{alts}</div>
            <div class="oc-sub">Suggested substitutes</div>
            <div class="oc-bar"><div class="oc-bar-fill cyan" style="width:{int(alts/total*100) if total else 0}%"></div></div>
          </div>
          <div class="output-card">
            <div class="oc-icon rose">⚠</div>
            <div class="oc-label">Needs Review</div>
            <div class="oc-val rose">{flagged}</div>
            <div class="oc-sub">Flagged items</div>
            <div class="oc-bar"><div class="oc-bar-fill rose" style="width:{int(flagged/total*100) if total else 0}%"></div></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    # ── Component table from JSON ───────────────────────────────────────────────
    if components:
        def _grade_tag(grade: str) -> str:
            g = str(grade).strip()
            if not g or g == "—":
                return '<span style="color:var(--text4)">—</span>'
            return f'<span class="comp-tag tag-gold">{g}</span>'

        def _status_tag(status: str) -> str:
            s = str(status).strip().lower()
            if "flag" in s or "review" in s or "warn" in s:
                return f'<span class="comp-tag tag-rose">{status}</span>'
            if "alt" in s:
                return f'<span class="comp-tag tag-cyan">{status}</span>'
            if "active" in s or "ok" in s or "verified" in s:
                return f'<span class="comp-tag tag-green">{status}</span>'
            return f'<span style="color:var(--text3)">{status}</span>'

        def _conf_bar(conf) -> str:
            try:
                pct = int(str(conf).replace("%",""))
            except Exception:
                return str(conf)
            color = "var(--emerald)" if pct >= 90 else "var(--gold)" if pct >= 75 else "var(--rose)"
            return (f'<span style="color:{color};font-family:\'JetBrains Mono\',monospace;font-size:11px;">{pct}%</span>'
                    f'<div style="height:2px;background:var(--rim);margin-top:3px;border-radius:1px;">'
                    f'<div style="width:{pct}%;height:2px;background:{color};border-radius:1px;"></div></div>')

        rows_html = ""
        for c in components:
            part   = c.get("part_number", c.get("part", ""))
            desc   = c.get("description", c.get("desc", ""))
            mfr    = c.get("manufacturer", c.get("mfr", ""))
            grade  = c.get("grade", c.get("compliance", ""))
            status = c.get("status", "Active")
            conf   = c.get("confidence", c.get("conf", ""))
            rows_html += f"""
            <tr>
              <td>{part}</td>
              <td>{desc}</td>
              <td>{mfr}</td>
              <td>{_grade_tag(grade)}</td>
              <td>{_status_tag(status)}</td>
              <td>{_conf_bar(conf) if conf else '—'}</td>
            </tr>"""

        col_h, col_dl = st.columns([6, 1])
        with col_h:
            st.markdown("""
            <div class="section-card" style="margin-bottom:8px;">
              <div class="card-header">
                <div class="card-title">Selected Components · BOM</div>
              </div>
            </div>
            """, unsafe_allow_html=True)
        with col_dl:
            raw = comp_json.read_text(encoding="utf-8") if comp_json.exists() else "[]"
            st.download_button("⬇ .json", raw, file_name="components.json",
                               mime="application/json", key="dl_comp_json",
                               use_container_width=True)

        st.markdown(f"""
        <div class="section-card">
          <table class="comp-table">
            <thead>
              <tr>
                <th>Part Number</th>
                <th>Description</th>
                <th>Manufacturer</th>
                <th>Grade</th>
                <th>Status</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
        </div>
        """, unsafe_allow_html=True)

    # ── Markdown recommendations (always show if present) ──────────────────────
    if comp_md.exists():
        content = comp_md.read_text(encoding="utf-8")
        col_h, col_dl = st.columns([6, 1])
        with col_h:
            st.markdown("""
            <div class="hp-doc-card">
              <div class="hp-doc-icon">🧩</div>
              <div class="hp-doc-meta">
                <div class="hp-doc-name">Component Recommendations</div>
                <div class="hp-doc-phase">P1 · component_recommendations.md</div>
              </div>
            </div>
            """, unsafe_allow_html=True)
        with col_dl:
            st.download_button("⬇ .md", content, file_name="component_recommendations.md",
                               mime="text/markdown", key="dl_comp_md",
                               use_container_width=True)
        with st.expander("🧩 Component Recommendations", expanded=not components):
            _render_markdown_with_mermaid(content, key_prefix="comp_md")

    if not components and not comp_md.exists():
        st.markdown('<div class="hp-alert info">Run the pipeline to generate component recommendations (Phase 1).</div>',
                    unsafe_allow_html=True)
        if st.button("🚀 Run Pipeline", type="primary", key="comp_run_btn"):
            _start_pipeline(st.session_state.project_id)


# ── Dashboard ──────────────────────────────────────────────────────────────────

def render_dashboard():
    _render_hero("All Projects", "<em>Dashboard</em>", "Pipeline status across all designs")

    projects = _api_get("/api/v1/projects") or []
    if not projects:
        st.markdown('<div class="hp-alert info">No projects yet. Create one in New Project.</div>',
                    unsafe_allow_html=True)
        return

    auto_total = sum(1 for _, _, _, _, auto in PHASE_META if auto)

    st.markdown(f"""
    <div class="output-grid" style="margin-bottom:24px;">
      <div class="output-card">
        <div class="oc-icon gold">📊</div>
        <div class="oc-label">Total Projects</div>
        <div class="oc-val gold">{len(projects)}</div>
        <div class="oc-sub">All hardware designs</div>
        <div class="oc-bar"><div class="oc-bar-fill gold" style="width:100%"></div></div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    for p in projects:
        phase_statuses = p.get("phase_statuses") or {}
        done_count = sum(1 for v in phase_statuses.values()
                         if v.get("status") == "completed")
        fail_count = sum(1 for v in phase_statuses.values()
                         if v.get("status") == "failed")
        pct = int(done_count / auto_total * 100) if auto_total else 0
        dt_icon = {"rf": "📡", "digital": "💻"}.get(p.get("design_type", ""), "⚙️")

        # Phase status dots — use CSS classes instead of inline styles
        # to keep the HTML simple and avoid Streamlit rendering issues
        dot_symbols = []
        for pid, num, _, _, auto in PHASE_META:
            if not auto:
                continue
            s = phase_statuses.get(pid, {}).get("status", "pending")
            sym = {"completed": "🟢", "failed": "🔴",
                   "in_progress": "🟡", "draft_pending": "🟠"}.get(s, "⚪")
            dot_symbols.append(sym)
        dots_str = " ".join(dot_symbols)

        fail_text = f" · {fail_count} failed" if fail_count else ""

        st.markdown(f"""
        <div class="hp-dash-card">
          <div class="hp-dash-head">
            <span class="hp-dash-icon">{dt_icon}</span>
            <div style="flex:1;">
              <div class="hp-dash-name">{p.get('name', '—')}</div>
              <div class="hp-dash-meta">
                <span class="hp-tag">{p.get('design_type','—')}</span>
                &nbsp;·&nbsp;{done_count}/{auto_total} phases
                &nbsp;·&nbsp;{pct}%
                <span style="color:var(--rose)">{fail_text}</span>
              </div>
              <div style="margin-top:6px;font-size:10px;letter-spacing:2px;">{dots_str}</div>
            </div>
          </div>
          <div class="prog-bar" style="margin-top:10px;">
            <div class="prog-fill" style="width:{pct}%"></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns([3, 1, 1])
        with col2:
            if st.button("📄 Docs", key=f"docs_{p['id']}", use_container_width=True):
                st.session_state.project_id = p["id"]
                st.session_state.current_project = p
                st.query_params["project_id"] = str(p["id"])
                # Clear stale chat from previous project — will reload from DB
                st.session_state.pop("chat_messages", None)
                st.session_state.pop("draft_pending", None)
                st.session_state.pop("_phase_overrides", None)
                st.query_params["tab"] = "docs"
                st.rerun()
        with col3:
            if st.button("Open →", key=f"open_{p['id']}", use_container_width=True):
                st.session_state.project_id = p["id"]
                st.session_state.current_project = p
                st.query_params["project_id"] = str(p["id"])
                # Clear stale chat from previous project — will reload from DB
                st.session_state.pop("chat_messages", None)
                st.session_state.pop("draft_pending", None)
                st.session_state.pop("_phase_overrides", None)
                st.query_params["tab"] = "pipeline"
                st.rerun()


# ── State restoration ──────────────────────────────────────────────────────────

def _restore_state_from_url() -> None:
    """
    Restore ALL project session state after a full browser page reload.

    When the user clicks a nav link (<a href target="_self">), the browser does a
    full HTTP reload, creating a new Streamlit WebSocket session with EMPTY
    session_state. The project_id is still in the URL (?project_id=54), so we
    read it and hydrate session_state entirely from the DB.

    Early-return guard: only skip when FULLY hydrated (project_id + current_project
    both present). After a browser reload, neither is set, so we always run.
    During normal st.rerun() calls, both are set, so we skip the redundant DB call.
    """
    url_pid = st.query_params.get("project_id", "")
    if not url_pid:
        return
    try:
        pid = int(url_pid)
    except ValueError:
        return

    # Only skip if FULLY hydrated — both identity fields present and matching.
    # After a browser reload session_state is empty, so current_project is missing
    # even if project_id happened to be set by some other code path.
    if (st.session_state.get("project_id") == pid
            and st.session_state.get("current_project")):
        return

    proj_data = _load_project(pid)
    if not proj_data:
        return

    # Restore core identity
    st.session_state.project_id     = pid
    st.session_state.current_project = proj_data

    # Restore chat history from DB conversation log so chat tab shows history
    history = proj_data.get("conversation_history", [])
    if history:
        st.session_state.chat_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]

    # Restore P1 UI state from DB phase status
    p1_status = (proj_data.get("phase_statuses", {})
                           .get("P1", {})
                           .get("status", "pending"))
    st.session_state.draft_pending = (p1_status == "draft_pending")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    tab = st.query_params.get("tab", "overview")

    # Restore ALL project state from URL after any full browser page reload.
    # This populates session_state from DB before any rendering happens.
    _restore_state_from_url()

    # Load project context fresh from DB on every run (DB is source of truth).
    # _restore_state_from_url() already set session_state.project_id if URL had it.
    proj      = None
    statuses  = {}
    if "project_id" in st.session_state:
        proj = _load_project(st.session_state.project_id)
        if proj:
            statuses = _load_status(st.session_state.project_id)
            # Belt-and-suspenders: if /status endpoint failed, use phase_statuses
            # embedded in the project data (same DB, same data, different endpoint)
            if not statuses:
                statuses = proj.get("phase_statuses", {})

    # Render topbar
    render_topbar(proj=proj, tab=tab)

    # Render sidebar (left panel)
    render_sidebar(statuses=statuses, proj=proj)

    # Main + right panel layout
    main_col, right_col = st.columns([3, 1])

    with main_col:
        if tab == "overview":    render_overview()
        elif tab == "new":       render_new_project()
        elif tab == "chat":      render_design_chat()
        elif tab == "pipeline":  render_pipeline()
        elif tab == "docs":       render_documents()
        elif tab == "components": render_components()
        elif tab == "netlist":    render_netlist()
        elif tab == "code":       render_code_review()
        elif tab == "dashboard":  render_dashboard()

    with right_col:
        _render_right_panel(statuses=statuses, proj=proj)

    # Navigation is handled by topbar <a> links and sidebar step <a> links


if __name__ == "__main__":
    main()

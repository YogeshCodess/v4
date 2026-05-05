# Manual PPTX Update Guide
**Based on:** SLIDE_REVIEW_NOTES.md corrections

---

## SLIDE 1 — Phase Overview

### Change 1: Update Phase Count
- **Find:** "8 Phases" or "8 phases"
- **Replace with:** "11 Phases"

### Change 2: Add P7a to Phase List
Add this row to the phase table:
| Phase | Label | Type | Tag |
|-------|-------|------|-----|
| **P7a** | Register Map & Programming Sequence | AI AUTO | ⚡ |

### Change 2b: Update P8 Description in Phase List
- **Find:** "P8 — Code Review" or "P8 — SRS + SDD + Code Review"
- **Replace with:** "P8 — Code Gen + Qt GUI + CI/CD → Git PR"

---

## SLIDE 2 — Problem Statement

### Change 3: Rephrase Bullet 1
- **Find:** "A single hardware design — from requirements to code review — takes 12–18 months."
- **Replace with:** "A single hardware design — from requirements to production-ready firmware — takes 12–18 months."

### Change 4: Add Footnote for ₹42L
Add at bottom of slide:
```
* Based on MIL-grade PCB respin: fab + components + engineering time (~$50K / ₹84)
```

### Change 5: Update Standards List
- **Find:** "RoHS, FCC, MISRA-C, IEEE 29148"
- **Replace with:** "RoHS/REACH, MIL-STD-461/810, IEEE 29148, IEEE 830, IEEE 1016, MISRA-C"

### Change 6: Update P8 Scope
- **Find:** "P8 — SRS + SDD + Code Review"
- **Replace with:** "P8 — Code Gen + Qt GUI + CI/CD → Git PR"

---

## SLIDE 3 — Solution

### Change 7: Remove Port Number
- **Find:** "FASTAPI — port 8000" or "port 8000"
- **Replace with:** "FASTAPI BACKEND · LOCAL REST API"

### Change 8: Update Agent Count
- **Find:** "7 AI Agents" or "7 agents"
- **Replace with:** "9 AI Agents"

### Change 9: Update Output Count
- **Find:** "8 Docs" or "8 documents"
- **Replace with:** "30+ Outputs" or "44 Artefacts"

### Change 10: Update Standards References
- **Find:** "Swagger docs · CORS"
- **Replace with:** "OpenAPI docs · Secure CORS config"

---

## SLIDE 4 — Innovation

### Change 11: Add All 9 Agents by Name
Add/update agent list:
| # | Agent | Phase |
|---|-------|-------|
| 1 | Requirements Agent | P1 |
| 2 | HRS Agent | P2 |
| 3 | Compliance Agent | P3 |
| 4 | Netlist Agent | P4 |
| 5 | GLR Agent | P6 |
| 6 | RDT/PSQ Agent | P7a |
| 7 | SRS Agent | P8a |
| 8 | SDD Agent | P8b |
| 9 | Code Agent | P8c |

### Change 12: Add ChromaDB Semantic Search
Add bullet under Innovation/Technology:
- **ChromaDB + OpenAI embeddings** — Semantic search on component datasheets ("similar to LM555")

### Change 13: Add Playwright E2E Testing
Add bullet under Quality/Testing:
- **Playwright** — Automated E2E UI testing for frontend validation

---

## SLIDE 5 — Outcomes

### Change 14: Update Infrastructure Cost
- **Find:** "$0 Infrastructure cost"
- **Replace with:** "₹0 cloud hosting / ~₹500 per project via API"

### Change 15: Update NOW Section
Update "NOW Delivered" to include:
```
11-phase AI pipeline · 9 AI agents · P7a (RDT/PSQ) · P8c (Code Gen + Qt GUI + CI/CD → Git PR)
```

---

## ALL SLIDES: MISRA-C Version

### Change 16: Dual-Version MISRA-C Label
- **Find:** "MISRA-C" (standalone)
- **Replace with:** "MISRA-C:2012 / 2023"

---

## Quick Run Commands

```bash
# Option 1: Automated (recommended)
python update_pptx.py HardwarePipeline_FINAL_v3.pptx

# Option 2: Install python-pptx first
pip install python-pptx
python update_pptx.py HardwarePipeline_FINAL_v3.pptx
```

---

## Verification Checklist

After updating, verify:
- [ ] "11 Phases" not "8"
- [ ] "9 AI Agents" not "7"
- [ ] "MISRA-C:2012 / 2023" not just "MISRA-C"
- [ ] P7a is listed in phases
- [ ] P8 shows "Code Gen + Qt GUI + CI/CD → Git PR"
- [ ] "₹0 cloud hosting / ~₹500/project"
- [ ] "30+ Outputs" not "8 docs"
- [ ] Standards list includes MIL-STD-461/810
- [ ] No "port 8000" mention
- [ ] ChromaDB + semantic search mentioned (Innovation slide)
- [ ] Playwright E2E testing mentioned (Innovation slide)

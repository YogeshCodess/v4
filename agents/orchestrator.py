"""
Orchestrator Agent - Master Controller for the Silicon to Software (S2S).

Routes execution through phases: P1 -> P2 -> P3 -> P4 -> P6 -> P7a -> P7 -> P8a -> P8b -> P8c
(P5 is a manual phase, skipped in automation)
"""

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from database.models import ProjectDB, PhaseOutputDB

logger = logging.getLogger(__name__)

# Phase execution order (automated phases only)
PHASE_ORDER = ["P1", "P2", "P3", "P4", "P6", "P7", "P7a", "P8a", "P8b", "P8c"]

# Phase to agent mapping
PHASE_AGENTS = {
    "P1": "agents.requirements_agent.RequirementsAgent",
    "P2": "agents.document_agent.DocumentAgent",
    "P3": "agents.compliance_agent.ComplianceAgent",
    "P4": "agents.netlist_agent.NetlistAgent",
    "P6": "agents.glr_agent.GLRAgent",
    "P7a": "agents.rdt_psq_agent.RdtPsqAgent",
    "P7":  "agents.fpga_agent.FpgaAgent",
    "P8a": "agents.srs_agent.SRSAgent",
    "P8b": "agents.sdd_agent.SDDAgent",
    "P8c": "agents.code_agent.CodeAgent",
}


class OrchestratorAgent:
    """Master controller that routes phases and manages pipeline state."""

    def __init__(self):
        self._agent_cache: dict = {}

    def _get_agent(self, phase_number: str) -> BaseAgent:
        """Lazily load and cache phase agents."""
        if phase_number not in self._agent_cache:
            agent_path = PHASE_AGENTS.get(phase_number)
            if not agent_path:
                raise ValueError(f"No agent registered for phase {phase_number}")

            module_path, class_name = agent_path.rsplit(".", 1)
            import importlib
            module = importlib.import_module(module_path)
            agent_class = getattr(module, class_name)
            self._agent_cache[phase_number] = agent_class()

        return self._agent_cache[phase_number]

    async def execute_phase(
        self,
        project_id: int,
        phase_number: str,
        user_input: str,
        session: Session,
    ) -> dict:
        """Execute a single phase for a project."""
        project = session.query(ProjectDB).filter(ProjectDB.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")

        logger.info(f"Executing phase {phase_number} for project '{project.name}'")

        # Build project context from DB
        project_context = self._build_project_context(project, session)

        # Get the phase agent
        agent = self._get_agent(phase_number)

        # Record start
        phase_output = PhaseOutputDB(
            project_id=project_id,
            phase_number=phase_number,
            phase_name=agent.phase_name,
            status="in_progress",
            started_at=datetime.now(),
        )
        session.add(phase_output)
        session.commit()

        try:
            # Execute the agent
            result = await agent.execute(project_context, user_input)

            # Record completion
            phase_output.status = "completed"
            phase_output.completed_at = datetime.now()
            phase_output.duration_seconds = (
                phase_output.completed_at - phase_output.started_at
            ).total_seconds()
            phase_output.model_used = result.get("model_used", "")
            # Safely encode outputs for database storage
            outputs = result.get("outputs", {})
            try:
                phase_output.content = str(outputs)
            except UnicodeEncodeError:
                # Fallback: encode problematic characters
                phase_output.content = json.dumps(outputs, ensure_ascii=True)

            # Update project phase status
            # Use copy to ensure SQLAlchemy detects the change
            statuses = dict(project.phase_statuses) if project.phase_statuses else {}
            statuses[phase_number] = {
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
            }
            project.phase_statuses = statuses
            project.current_phase = self._get_next_phase(phase_number)

            session.commit()

            logger.info(
                f"Phase {phase_number} completed in {phase_output.duration_seconds:.1f}s"
            )
            return result

        except Exception as e:
            phase_output.status = "failed"
            phase_output.error_message = str(e)
            phase_output.completed_at = datetime.now()
            session.commit()
            logger.error(f"Phase {phase_number} failed: {e}")
            raise

    async def execute_all(
        self,
        project_id: int,
        initial_input: str,
        session: Session,
    ) -> dict:
        """Execute all automated phases sequentially."""
        results = {}
        for phase in PHASE_ORDER:
            try:
                result = await self.execute_phase(
                    project_id=project_id,
                    phase_number=phase,
                    user_input=initial_input if phase == "P1" else "",
                    session=session,
                )
                results[phase] = result

                # If phase is not complete (P1 needs conversation), stop
                if not result.get("phase_complete", True):
                    break

            except Exception as e:
                results[phase] = {"error": str(e)}
                logger.error(f"Pipeline stopped at phase {phase}: {e}")
                break

        return results

    def _build_project_context(self, project: ProjectDB, session: Session) -> dict:
        """Build context dict from database for agent consumption."""
        # Get all prior phase outputs
        outputs = (
            session.query(PhaseOutputDB)
            .filter(PhaseOutputDB.project_id == project.id)
            .filter(PhaseOutputDB.status == "completed")
            .all()
        )

        prior_outputs = {}
        for out in outputs:
            prior_outputs[out.phase_number] = {
                "content": out.content,
                "file_path": out.file_path,
            }

        return {
            "project_id": project.id,
            "name": project.name,
            "description": project.description,
            "design_type": project.design_type,
            "output_dir": project.output_dir,
            "conversation_history": project.conversation_history or [],
            "design_parameters": project.design_parameters or {},
            "prior_phase_outputs": prior_outputs,
        }

    def _get_next_phase(self, current_phase: str) -> str:
        """Get the next phase in the pipeline."""
        try:
            idx = PHASE_ORDER.index(current_phase)
            if idx + 1 < len(PHASE_ORDER):
                return PHASE_ORDER[idx + 1]
        except ValueError:
            pass
        return "DONE"

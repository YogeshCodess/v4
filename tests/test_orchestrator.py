"""
Integration tests for the Orchestrator and full pipeline flow.
"""


import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.orchestrator import OrchestratorAgent, PHASE_ORDER, PHASE_AGENTS
from database.models import Base, ProjectDB


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def db_session(tmp_path):
    """Create a test database session."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()
    # Cleanup
    import os
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest.fixture
def test_project(db_session, tmp_path):
    """Create a test project in the database."""
    project = ProjectDB(
        name="TestProject",
        description="A test hardware project",
        design_type="digital",
        output_dir=str(tmp_path / "output"),
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def mock_orchestrator():
    """Create an orchestrator with mocked agents."""
    orchestrator = OrchestratorAgent()

    # Mock execute method on BaseAgent
    async def mock_execute(self, project_context, user_input):
        return {
            "response": f"Phase {self.phase_number} completed",
            "phase_complete": True,
            "outputs": {"test.md": f"# Phase {self.phase_number} Output"},
        }

    return orchestrator, mock_execute


# =============================================================================
# OrchestratorAgent Tests
# =============================================================================

class TestOrchestratorAgent:
    """Test OrchestratorAgent initialization and configuration."""

    def test_init(self):
        """Test orchestrator initialization."""
        orchestrator = OrchestratorAgent()
        assert orchestrator._agent_cache == {}

    def test_phase_order_constant(self):
        """PHASE_ORDER covers every auto AI phase in execution order.

        The list now includes P7 (FPGA) and P7a (Register Map) between P6
        (GLR) and P8a (SRS). Manual-only phases (P5 PCB layout) are
        intentionally absent — the orchestrator doesn't auto-run them.
        """
        expected = ["P1", "P2", "P3", "P4", "P6", "P7", "P7a", "P8a", "P8b", "P8c"]
        assert PHASE_ORDER == expected
        # Locking in two structural invariants rather than just a literal
        # so a reorder with a typo gets flagged even if the length matches:
        assert PHASE_ORDER.index("P1") == 0
        assert PHASE_ORDER[-1] == "P8c"
        assert "P5" not in PHASE_ORDER  # manual PCB layout
        assert len(set(PHASE_ORDER)) == len(PHASE_ORDER)  # no duplicates

    def test_phase_agents_mapping(self):
        """Test phase to agent mapping."""
        assert "P1" in PHASE_AGENTS
        assert "P2" in PHASE_AGENTS
        assert "P3" in PHASE_AGENTS
        assert "P4" in PHASE_AGENTS
        assert "P6" in PHASE_AGENTS
        assert "P8a" in PHASE_AGENTS
        assert "P8b" in PHASE_AGENTS
        assert "P8c" in PHASE_AGENTS

    def test_get_agent_lazy_loading(self):
        """Test agents are lazy loaded and cached."""
        orchestrator = OrchestratorAgent()

        # First call should create and cache the agent
        agent1 = orchestrator._get_agent("P1")
        assert agent1 is not None
        assert "P1" in orchestrator._agent_cache

        # Second call should return cached agent
        agent2 = orchestrator._get_agent("P1")
        assert agent1 is agent2

    def test_get_agent_invalid_phase(self):
        """Test get_agent raises error for invalid phase."""
        orchestrator = OrchestratorAgent()

        with pytest.raises(ValueError, match="No agent registered"):
            orchestrator._get_agent("INVALID")

    def test_get_next_phase(self):
        """Test getting the next phase in sequence."""
        orchestrator = OrchestratorAgent()

        assert orchestrator._get_next_phase("P1") == "P2"
        assert orchestrator._get_next_phase("P2") == "P3"
        assert orchestrator._get_next_phase("P8c") == "DONE"
        assert orchestrator._get_next_phase("INVALID") == "DONE"


class TestOrchestratorExecutePhase:
    """Test single phase execution."""

    @pytest.mark.asyncio
    async def test_execute_phase_success(self, test_project, db_session):
        """Test successful phase execution."""
        orchestrator = OrchestratorAgent()

        # Get the agent and mock its execute method directly
        agent = orchestrator._get_agent("P1")
        original_execute = agent.execute

        async def mock_execute(project_context, user_input):
            return {
                "response": f"Phase {agent.phase_number} completed",
                "phase_complete": True,
                "outputs": {"test.md": f"# Phase {agent.phase_number} Output"},
            }

        agent.execute = mock_execute

        result = await orchestrator.execute_phase(
            project_id=test_project.id,
            phase_number="P1",
            user_input="Create an LED blinker",
            session=db_session,
        )

        # Restore original
        agent.execute = original_execute

        assert result["phase_complete"] is True

    @pytest.mark.asyncio
    async def test_execute_phase_invalid_project(self, db_session):
        """Test execution with invalid project ID."""
        orchestrator = OrchestratorAgent()

        with pytest.raises(ValueError, match="not found"):
            await orchestrator.execute_phase(
                project_id=99999,
                phase_number="P1",
                user_input="Test",
                session=db_session,
            )

    @pytest.mark.asyncio
    async def test_execute_phase_updates_database(self, test_project, db_session):
        """Test that phase execution updates the database."""
        orchestrator = OrchestratorAgent()

        # Get the agent and mock its execute method directly
        agent = orchestrator._get_agent("P1")
        original_execute = agent.execute

        async def mock_execute(project_context, user_input):
            return {
                "response": "Phase completed",
                "phase_complete": True,
                "outputs": {"test.md": "Test content"},
                "model_used": "claude-opus-4-6",
            }

        agent.execute = mock_execute

        await orchestrator.execute_phase(
            project_id=test_project.id,
            phase_number="P1",
            user_input="Test",
            session=db_session,
        )

        # Restore original
        agent.execute = original_execute

        # Refresh project from DB
        db_session.refresh(test_project)

        # Check phase status was updated
        assert test_project.phase_statuses is not None
        assert "P1" in test_project.phase_statuses
        assert test_project.phase_statuses["P1"]["status"] == "completed"
        assert test_project.current_phase == "P2"

    @pytest.mark.asyncio
    async def test_execute_phase_handles_failure(self, test_project, db_session):
        """Test phase execution failure handling."""
        orchestrator = OrchestratorAgent()

        # Get the agent and mock its execute method directly
        agent = orchestrator._get_agent("P1")
        original_execute = agent.execute

        async def mock_execute_error(project_context, user_input):
            raise RuntimeError("Agent failed")

        agent.execute = mock_execute_error

        with pytest.raises(RuntimeError, match="Agent failed"):
            await orchestrator.execute_phase(
                project_id=test_project.id,
                phase_number="P1",
                user_input="Test",
                session=db_session,
            )

        # Restore original
        agent.execute = original_execute

    def test_build_project_context(self, test_project, db_session):
        """Test building project context from database."""
        orchestrator = OrchestratorAgent()

        context = orchestrator._build_project_context(test_project, db_session)

        assert context["project_id"] == test_project.id
        assert context["name"] == test_project.name
        assert context["description"] == test_project.description
        assert context["design_type"] == test_project.design_type
        assert context["output_dir"] == test_project.output_dir


class TestOrchestratorExecuteAll:
    """Test full pipeline execution."""

    @pytest.mark.asyncio
    async def test_execute_all_phases(self, test_project, db_session):
        """Test executing all phases sequentially."""
        orchestrator = OrchestratorAgent()

        executed_phases = []
        original_executes = {}

        # Mock all phase agents
        for phase in PHASE_ORDER:
            agent = orchestrator._get_agent(phase)
            original_executes[phase] = agent.execute

            async def mock_execute(project_context, user_input, phase_number=phase):
                executed_phases.append(phase_number)
                return {
                    "response": f"Phase {phase_number} completed",
                    "phase_complete": True,
                    "outputs": {},
                }

            agent.execute = mock_execute

        results = await orchestrator.execute_all(
            project_id=test_project.id,
            initial_input="Create LED blinker",
            session=db_session,
        )

        # Restore all original executes
        for phase in PHASE_ORDER:
            agent = orchestrator._get_agent(phase)
            agent.execute = original_executes[phase]

        # All phases should be executed
        assert len(executed_phases) == len(PHASE_ORDER)
        assert executed_phases == PHASE_ORDER

        # Results should contain all phases
        assert len(results) == len(PHASE_ORDER)
        for phase in PHASE_ORDER:
            assert phase in results
            assert results[phase]["phase_complete"] is True

    @pytest.mark.asyncio
    async def test_execute_all_stops_on_incomplete_phase(self, test_project, db_session):
        """Test execution stops when a phase is incomplete."""
        orchestrator = OrchestratorAgent()

        original_executes = {}

        # Mock all phase agents - P1 will return incomplete
        for phase in PHASE_ORDER:
            agent = orchestrator._get_agent(phase)
            original_executes[phase] = agent.execute

            if phase == "P1":
                async def mock_execute_partial(project_context, user_input):
                    return {
                        "response": "Tell me more",
                        "phase_complete": False,
                        "outputs": {},
                    }
                agent.execute = mock_execute_partial
            else:
                async def mock_execute(project_context, user_input):
                    return {
                        "response": "Phase completed",
                        "phase_complete": True,
                        "outputs": {},
                    }
                agent.execute = mock_execute

        results = await orchestrator.execute_all(
            project_id=test_project.id,
            initial_input="Create LED blinker",
            session=db_session,
        )

        # Restore all original executes
        for phase in PHASE_ORDER:
            agent = orchestrator._get_agent(phase)
            agent.execute = original_executes[phase]

        # Only P1 should be executed
        assert len(results) == 1
        assert "P1" in results
        assert results["P1"]["phase_complete"] is False


class TestOrchestratorEdgeCases:
    """Test edge cases and error conditions."""

    def test_get_next_phase_edge_cases(self):
        """Test _get_next_phase with edge cases."""
        orchestrator = OrchestratorAgent()

        # Last phase
        assert orchestrator._get_next_phase("P8c") == "DONE"

        # Invalid phase
        assert orchestrator._get_next_phase("INVALID") == "DONE"

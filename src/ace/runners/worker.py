"""Worker entrypoint for processing a single ticket."""

from datetime import datetime

import structlog

from ace.config.logging import configure_logging
from ace.config.settings import get_settings
from ace.orchestration.graph import get_compiled_graph
from ace.orchestration.state import WorkerState

logger = structlog.get_logger(__name__)


async def process_ticket(issue_number: int) -> None:
    """Process a single ticket through the workflow.

    Args:
        issue_number: GitHub issue number to process
    """
    settings = get_settings()
    configure_logging(debug=settings.debug)

    logger.info("worker_starting", issue=issue_number, agent_id=settings.agent_id)

    initial_state = WorkerState(
        issue_number=issue_number,
        agent_id=settings.agent_id,
        started_at=datetime.now(),
        last_update=datetime.now(),
    )

    graph = get_compiled_graph()

    try:
        final_state = await graph.ainvoke(initial_state)
        logger.info(
            "worker_completed",
            issue=issue_number,
            pr_number=final_state.pr_number,
            status="success",
        )
    except Exception as e:
        logger.error(
            "worker_failed",
            issue=issue_number,
            error=str(e),
            status="failed",
        )
        raise


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 2:
        logger.error("missing_issue_number", usage="python -m ace.runners.worker <issue_number>")
        sys.exit(1)

    issue_number = int(sys.argv[1])
    asyncio.run(process_ticket(issue_number))

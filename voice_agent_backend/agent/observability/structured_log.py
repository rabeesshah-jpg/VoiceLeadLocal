import logging

from config.step_log import log_block

logger = logging.getLogger("agent.observability")


def log_event(event: str, component: str = "WORKER", **kwargs):
    log_block(
        logger,
        logging.INFO,
        operation=component,
        step=event,
        status="EVENT",
        **kwargs,
    )

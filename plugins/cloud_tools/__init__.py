"""Hermes Cloud Tools Bundle — integrates KV, Diary, SMS, and Work Hour System."""

import logging
from .tools import ALL_TOOLS

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register all cloud service tools into Hermes Agent."""
    registered_count = 0
    for name, schema, handler, emoji in ALL_TOOLS:
        try:
            ctx.register_tool(
                name=name,
                toolset="cloud_tools",
                schema=schema,
                handler=handler,
                emoji=emoji,
            )
            registered_count += 1
        except Exception as e:
            logger.warning("Failed to register tool %s: %s", name, e)

    logger.info("Hermes Cloud Tools Bundle registered %d tools successfully", registered_count)

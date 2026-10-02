"""Small standard-library logging setup for Forge AI."""

import logging

from app.config.settings import RuntimeSettings


def configure_logging(settings: RuntimeSettings) -> logging.Logger:
    """Configure one application logger without logging settings or secrets."""
    logger = logging.getLogger("forge_ai")
    logger.setLevel(getattr(logging, settings.log_level))
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    return logger

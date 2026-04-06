# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import logging

_LOGGER = logging.getLogger("idiograph")


def setup_logging(level: str = "INFO") -> None:
    """
    Configure the idiograph root logger.
    Safe to call multiple times — only adds a handler if none exists.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    _LOGGER.setLevel(numeric_level)

    if not _LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        ))
        _LOGGER.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the idiograph namespace."""
    return logging.getLogger(f"idiograph.{name}")

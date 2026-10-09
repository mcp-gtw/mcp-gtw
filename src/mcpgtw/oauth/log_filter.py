from __future__ import annotations

import logging
import re


class OAuthLogFilter(logging.Filter):
    """Uvicorn logs WebSocket query strings on its error logger even with access logs off."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(r'([^\s"?]+)\?[^\s"]+', r"\1?[redacted]", record.getMessage())
        record.args = ()
        return True

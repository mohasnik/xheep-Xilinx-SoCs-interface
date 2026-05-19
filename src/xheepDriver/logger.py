# Copyright 2026 Politecnico di Torino.
#
# File: logger.py
# Author: Christian Conti {christian.conti@polito.it}
# Date: 31/03/2026

import sys
from typing import Optional

RESET = "\033[0m"
COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[41m\033[97m",
}

def log(level: str, msg: str, stderr: Optional[bool] = None) -> None:
    lvl = level.upper()
    color = COLORS.get(lvl, "")
    if stderr is None:
        use_stderr = (lvl in ("WARNING", "ERROR", "CRITICAL"))
    else:
        use_stderr = stderr
    stream = sys.stderr if use_stderr else sys.stdout
    stream.write(f"{color}[{lvl}] {msg}{RESET}\n")
    stream.flush()

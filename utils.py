import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime


class Logger:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, name: str = "ardo"):
        if hasattr(self, "_initialized"):
            return
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers = []
        Path("logs").mkdir(exist_ok=True)
        log_file = f"logs/ardo_{datetime.now().strftime('%Y%m%d')}.log"
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        fmt = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh.setFormatter(fmt)
        ch.setFormatter(fmt)
        self.logger.addHandler(fh)
        self.logger.addHandler(ch)
        self._initialized = True

    def debug(self, msg):   self.logger.debug(msg)
    def info(self, msg):    self.logger.info(msg)
    def warning(self, msg): self.logger.warning(msg)
    def error(self, msg):   self.logger.error(msg)


logger = Logger()


def log_debug(msg):   logger.debug(msg)
def log_info(msg):    logger.info(msg)
def log_warning(msg): logger.warning(msg)
def log_error(msg):   logger.error(msg)

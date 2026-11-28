"""Application files and per-user data for source and installed builds."""

import os
from pathlib import Path
import sys


FROZEN = getattr(sys, 'frozen', False)
SOURCE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else SOURCE_DIR
DATA_DIR = (Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'VPT39'
            if FROZEN else SOURCE_DIR)

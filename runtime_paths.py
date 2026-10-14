"""Shared paths and child commands for source and packaged installers."""
import sys
from pathlib import Path


def app_root():
    return Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent


def child_command(mode, *args):
    if getattr(sys, 'frozen', False):
        return [sys.executable, mode, *map(str, args)]
    return [sys.executable, str(app_root() / 'installer_entry.py'), mode, *map(str, args)]

"""
[DNA_TAG]
ORIGIN: Moto4_A9
PILLAR: valet_concierge
PATH: migrator.py
LAST_SYNC: 2026-08-02T01:13:37Z
[/DNA_TAG]
"""
import shutil
import os
import logging
from pathlib import Path

def migrate_project(source_path, destination_root):
    destination = destination_root / source_path.name
    if destination.exists():
        logging.info(f"Already exists: {destination}")
        return destination
    # Skip symlinks
    if source_path.is_symlink():
        logging.info(f"Skipping symlink: {source_path}")
        return source_path
    logging.info(f"Migrating {source_path} to {destination}")
    shutil.copytree(str(source_path), str(destination))
    shutil.rmtree(str(source_path))
    return destination

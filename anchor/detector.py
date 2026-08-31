"""
[DNA_TAG]
ORIGIN: Moto4_A9
PILLAR: valet_concierge
PATH: detector.py
LAST_SYNC: 2026-08-02T01:13:37Z
[/DNA_TAG]
"""
import os
from pathlib import Path

# Markers that define a directory as a project
PROJECT_MARKERS = [".git", "package.json", "README.md", "requirements.txt", "pyproject.toml"]

def is_project(path: Path) -> bool:
    """Checks if a directory contains indicators of being a project."""
    if not path.is_dir():
        return False
    
    # Ignore hidden directories (except .git)
    if path.name.startswith('.') and path.name != '.git':
        return False
        
    return any((path / marker).exists() for marker in PROJECT_MARKERS)

def get_new_projects(root_dir: Path) -> list[Path]:
    """Scans for new projects in the home directory."""
    new_projects = []
    for item in root_dir.iterdir():
        if is_project(item):
            new_projects.append(item)
    return new_projects

if __name__ == "__main__":
    home_dir = Path.home()
    projects = get_new_projects(home_dir)
    print(f"Detected projects: {[p.name for p in projects]}")

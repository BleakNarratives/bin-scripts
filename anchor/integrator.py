"""
[DNA_TAG]
ORIGIN: Moto4_A9
PILLAR: valet_concierge
PATH: integrator.py
LAST_SYNC: 2026-08-02T01:13:37Z
[/DNA_TAG]
"""
import json
import logging
from pathlib import Path
from datetime import datetime

# Define ecosystem nodes
ECOSYSTEM_NODES = ["Thoth", "Fartographer", "Bardildo"]
NOTIFICATIONS_DIR = Path.home() / ".Praudit" / "ecosystem"
NOTIFICATIONS_DIR.mkdir(parents=True, exist_ok=True)

def notify_node(node: str, event_type: str, details: dict):
    """Sends a structured notification to a specific ecosystem node."""
    message = {
        "timestamp": datetime.utcnow().isoformat(),
        "source": "Praudit",
        "event_type": event_type,
        "details": details
    }
    node_file = NOTIFICATIONS_DIR / f"{node.lower()}.json"
    
    # Append message to node's event log
    with open(node_file, "a") as f:
        f.write(json.dumps(message) + "\n")
    logging.info(f"Notification sent to {node}: {event_type}")

def broadcast_migration(project_name: str, old_path: Path, new_path: Path):
    """Broadcasts a migration event to all ecosystem nodes."""
    details = {
        "project": project_name,
        "old_path": str(old_path),
        "new_path": str(new_path)
    }
    for node in ECOSYSTEM_NODES:
        notify_node(node, "PROJECT_MIGRATED", details)

if __name__ == "__main__":
    # Test notification
    logging.basicConfig(level=logging.INFO)
    broadcast_migration("TestProject", Path("/home/user/TestProject"), Path("$HOME/RootBase/TestProject"))
    print("Broadcast test complete.")

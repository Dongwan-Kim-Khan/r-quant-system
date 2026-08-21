"""
Automated Online SQLite Snapshot Backup & Disaster Recovery Engine.
"""
import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from al_sangmoo.core.config import DB_FILE, BASE_DIR

BACKUP_DIR = BASE_DIR / "backups"

def create_sqlite_backup() -> Dict[str, Any]:
    """
    Creates an online, non-blocking snapshot of the SQLite database
    using the native SQLite backup API (WAL-safe).
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"quant_trades_{timestamp_str}.db"

    source_conn = sqlite3.connect(str(DB_FILE))
    dest_conn = sqlite3.connect(str(backup_file))

    try:
        source_conn.backup(dest_conn)
        dest_conn.close()
        source_conn.close()
        
        file_size_bytes = backup_file.stat().st_size
        return {
            "status": "success",
            "backup_file": str(backup_file.name),
            "file_path": str(backup_file),
            "size_bytes": file_size_bytes,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        dest_conn.close()
        source_conn.close()
        raise RuntimeError(f"Database backup failed: {str(e)}")

def list_backups() -> List[Dict[str, Any]]:
    """Returns a list of all existing database snapshot backups."""
    if not BACKUP_DIR.exists():
        return []
        
    backups = []
    for f in sorted(BACKUP_DIR.glob("quant_trades_*.db"), reverse=True):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "size_kb": round(stat.st_size / 1024, 2),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        })
    return backups

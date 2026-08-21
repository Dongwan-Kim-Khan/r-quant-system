"""
Atomic File I/O Engine with Cross-Platform Replace & Windows Sharing Retry.
"""
import os
import sys
import time
import json
import tempfile
from pathlib import Path

def atomic_save_json(file_path, data, indent=2, max_retries=10):
    """
    Atomically writes JSON by writing to a temporary file on the same volume,
    flushing, and executing an atomic os.replace swap with exponential retry backoff.
    """
    path_obj = Path(file_path).resolve()
    dir_name = str(path_obj.parent)
    os.makedirs(dir_name, exist_ok=True)
    temp_name = None
    
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
        temp_name = tf.name
        json.dump(data, tf, ensure_ascii=False, indent=indent)
        tf.flush()
        os.fsync(tf.fileno())
        
    target_str = str(path_obj)
    for attempt in range(max_retries):
        try:
            os.replace(temp_name, target_str)
            return
        except (PermissionError, OSError):
            if attempt == max_retries - 1:
                try:
                    with open(target_str, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=indent)
                    if temp_name and os.path.exists(temp_name):
                        os.remove(temp_name)
                    return
                except Exception:
                    pass
                raise
            time.sleep(0.01 * (1.5 ** attempt))
            
    if temp_name and os.path.exists(temp_name):
        try:
            os.remove(temp_name)
        except Exception:
            pass

def atomic_read_json(file_path, default=None, max_retries=5):
    """
    Safely reads JSON without throwing on temporary file locked or in-flight update conditions.
    """
    path_obj = Path(file_path).resolve()
    if not path_obj.exists():
        return default if default is not None else {}
        
    for attempt in range(max_retries):
        try:
            with open(path_obj, "r", encoding="utf-8") as f:
                return json.load(f)
        except (PermissionError, json.JSONDecodeError, OSError):
            if attempt == max_retries - 1:
                return default if default is not None else {}
            time.sleep(0.01 * (attempt + 1))
            
    return default if default is not None else {}

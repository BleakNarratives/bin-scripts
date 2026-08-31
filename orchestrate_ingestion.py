#!/usr/bin/env python3
import os
import re
import hashlib
import shutil
import sys

def get_md5(file_path):
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def extract_dna_version(file_path):
    if not os.path.isfile(file_path):
        return None
    version_pattern = re.compile(r'(?:dna[:\-_]tag|version|__version__)\s*[:=]\s*["\']?v?([0-9]+(?:\.[0-9]+)*)["\']?', re.IGNORECASE)
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(30):
                line = f.readline()
                if not line:
                    break
                match = version_pattern.search(line)
                if match:
                    return tuple(map(int, match.group(1).split('.')))
    except Exception:
        pass
    return None

def reconcile(staging_dir, production_dir):
    print(f"Reconciling {staging_dir} into {production_dir}")
    conflicts_log = "/tmp/ingestion_conflicts.log"
    if os.path.exists(conflicts_log):
        os.remove(conflicts_log)

    for root, dirs, files in os.walk(staging_dir):
        for name in files:
            staging_path = os.path.join(root, name)
            relative_path = os.path.relpath(staging_path, staging_dir)
            production_path = os.path.join(production_dir, relative_path)
            
            if not os.path.exists(production_path):
                print(f"[NEW] {relative_path}")
                os.makedirs(os.path.dirname(production_path), exist_ok=True)
                shutil.copy2(staging_path, production_path)
                continue
                
            if get_md5(staging_path) == get_md5(production_path):
                print(f"[DUPE] {relative_path} - Skipping")
                continue
            
            staged_ver = extract_dna_version(staging_path)
            prod_ver = extract_dna_version(production_path)
            
            if staged_ver and prod_ver:
                if staged_ver > prod_ver:
                    print(f"[AUTO-UPDATE] {relative_path} (DNA v{staged_ver} > v{prod_ver}) -> Overwriting Prod")
                    shutil.copy2(staging_path, production_path)
                    continue
                elif staged_ver < prod_ver:
                    print(f"[SKIP-OLD] {relative_path} (Staged DNA v{staged_ver} < Prod v{prod_ver})")
                    continue
            
            print(f"[CONFLICT] {relative_path} - Flagged for review")
            with open(conflicts_log, "a") as f:
                f.write(f"STAGING: {staging_path} | PROD: {production_path}\n")

if __name__ == "__main__":
    STAGING = sys.argv[1] if len(sys.argv) > 1 else "./._incoming/staging"
    PRODUCTION = sys.argv[2] if len(sys.argv) > 2 else "./Official-Vertical-AI-Boardroom"
    reconcile(STAGING, PRODUCTION)

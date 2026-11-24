"""One-time local setup: .env, folders (with permissions the SFTP container can write), certificates."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / ".env").exists():
    shutil.copy(ROOT / ".env.example", ROOT / ".env")
    print("created .env from .env.example")
for user in ("acme01", "mediline", "retailco"):
    for sub in ("upload", "download"):
        d = ROOT / "data" / "sftp" / user / sub
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o777)  # the SFTP container writes as uid 1001-1003; local demo only
(ROOT / "logs").mkdir(exist_ok=True)
os.chmod(ROOT / "logs", 0o777)
subprocess.run([sys.executable, str(ROOT / "scripts" / "setup_certs.py")], check=True)
print("ready: docker compose up -d --build")

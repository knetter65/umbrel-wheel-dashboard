from __future__ import annotations

import gzip
import hashlib
import io
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1] if len(sys.argv) > 1 else root.parent.parent / "dist" / "wheel-dashboard-0.3.0-source.tar.gz").resolve()
excluded = {".git", ".venv", "__pycache__", ".pytest_cache", "dist"}
files = [p for p in root.rglob("*") if p.is_file() and not any(part in excluded for part in p.relative_to(root).parts)]
files.sort(key=lambda p: p.relative_to(root).as_posix())
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in files:
                relative = Path("umbrel-wheel-dashboard") / path.relative_to(root)
                data = path.read_bytes()
                info = tarfile.TarInfo(relative.as_posix())
                info.size = len(data)
                info.mode = 0o755 if relative.as_posix().startswith("umbrel-wheel-dashboard/scripts/") else 0o644
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                archive.addfile(info, io.BytesIO(data))
print(hashlib.sha256(out.read_bytes()).hexdigest(), out)

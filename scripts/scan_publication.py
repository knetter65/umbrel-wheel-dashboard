from __future__ import annotations

import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
identifier = "l" + "so"
errors = []
for path in sorted(root.rglob("*")):
    relative = path.relative_to(root).as_posix()
    if any(part in {".git", ".venv", "__pycache__", "dist"} for part in path.parts):
        continue
    if identifier in relative.lower():
        errors.append(f"forbidden identifier in path: {relative}")
    if path.is_symlink():
        errors.append(f"symlink not allowed: {relative}")
        continue
    if not path.is_file():
        continue
    if path.suffix.lower() in {".db", ".sqlite", ".xml", ".csv", ".pem", ".key"}:
        errors.append(f"private or unsafe file type: {relative}")
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico"}:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"unexpected binary file: {relative}")
        continue
    if identifier in text.lower():
        errors.append(f"forbidden identifier in content: {relative}")
    if re.search(r"(?i)(password|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*[A-Za-z0-9_./+-]{8,}", text):
        errors.append(f"possible embedded credential: {relative}")
    route_terms = ("place_" + "order", "transmit_" + "order", "submit_" + "order")
    lowered = text.lower()
    for term in route_terms:
        if term in lowered:
            errors.append(f"order-capable symbol: {relative}")
if errors:
    print("PUBLICATION_SCAN_FAILED")
    print("\n".join(errors))
    raise SystemExit(1)
print("PUBLICATION_SCAN_OK")

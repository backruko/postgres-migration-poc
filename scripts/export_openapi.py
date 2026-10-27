"""
FastAPI の OpenAPI 仕様を JSON ファイルに書き出します（サーバーを起動せずに生成できます）。

    python scripts/export_openapi.py                      # -> frontend/openapi.json
    python scripts/export_openapi.py path/to/openapi.json

フロントエンドの型はこのファイルから生成します（frontend で `npm run gen:api`）。
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)  # main.py のログ設定による出力を抑える

from main import app  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "frontend" / "openapi.json"
out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"OpenAPI spec written to {out}")

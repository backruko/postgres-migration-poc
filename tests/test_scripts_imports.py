"""
scripts/ のスクリプトが、どの動かし方でも src を見つけられること。

- サーバー・CI・手順書は python -m scripts.<名前> で動かす（リポジトリのルートが import の起点になる）
- 開発 PC のタスク スケジューラーなどは python scripts/<名前>.py のままのことがある。このときは各スクリプトの
  sys.path.insert(0, ROOT) が起点を足すので、src・scripts の import はその行より後に置く
  （前に置くと ModuleNotFoundError: No module named 'src'。worker が起動しなかった原因）
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _imports_before_path_setup(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    early = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and "sys.path.insert" in ast.unparse(node):
            return early
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in {"src", "scripts"}:
            early.append(f"{path.name}:{node.lineno} from {node.module}")
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] in {"src", "scripts"} for a in node.names):
            early.append(f"{path.name}:{node.lineno} import")
    return []  # sys.path を足さないスクリプト（src を使わない）は対象外


def test_no_script_imports_src_before_it_puts_the_repository_root_on_sys_path():
    early = [hit for p in sorted((ROOT / "scripts").glob("*.py")) for hit in _imports_before_path_setup(p)]
    assert early == []


def test_the_worker_container_runs_the_worker_as_a_module():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r'command: \["python", "-m", "scripts\.worker"\]', compose)

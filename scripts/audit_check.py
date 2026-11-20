"""
サーバーの依存（requirements.txt・requirements-dev.txt）の既知の脆弱性を調べます（CI の「依存関係の既知の脆弱性（pip-audit）」。
1 つでもあれば止める）。requirements-audit-ignore.txt に理由つきで書いた脆弱性だけを見送ります。

    python -m scripts.audit_check              # リポジトリのルートで（開発 PC でも CI でも同じ）
    python -m scripts.audit_check <一覧のファイル>

終了コード: 0 = なし、1 = あり（または一覧の書き方の誤り）、ほか = pip-audit の誤り（つながらない など）。
pip-audit は requirements-dev.txt で入ります。requirements*.txt はすべての依存を版とハッシュつきで並べている
（requirements*.in から pip-compile で作る）ので、ファイルをそのまま調べます（--require-hashes: 仮の環境を作らず、
ハッシュの無い行があれば誤りにする）。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ("requirements.txt", "requirements-dev.txt")


def read_ignore_list(path: Path) -> tuple[list[str], list[str]]:
    """見送りの一覧を読む: 1 行に 1 つ「ID  # 理由」。# で始まる行と空行は飛ばす。戻り値は (ID, 誤り)"""
    ids: list[str] = []
    problems: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        vuln_id, _, reason = line.partition("#")
        vuln_id = vuln_id.strip()
        if not vuln_id or " " in vuln_id:
            problems.append(f"{path.name}:{number}: 1 行に ID は 1 つ（いま: {vuln_id!r}）")
        elif not reason.strip(" #\t"):
            problems.append(f"{path.name}:{number}: {vuln_id} に理由が無い（# のあとに、見送る理由・見直す日・決めた人を書く）")
        else:
            ids.append(vuln_id)
    return ids, problems


def main() -> int:
    ignore_file = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "requirements-audit-ignore.txt"
    ids, problems = read_ignore_list(ignore_file)
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print(f"見送り（{ignore_file.name} の理由つき）: {len(ids)} 件", flush=True)  # pip-audit の出力より先に出す
    command = [sys.executable, "-m", "pip_audit", "--require-hashes", "--disable-pip", "--progress-spinner", "off"]
    for name in REQUIREMENTS:
        command += ["-r", str(ROOT / name)]
    for vuln_id in ids:
        command += ["--ignore-vuln", vuln_id]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())

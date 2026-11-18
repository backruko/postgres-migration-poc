"""
サーバーへ USB で持っていく zip を作ります（社内のサーバーは GitHub に届かないため）。

    python scripts/make_package.py [--out packages] [--allow-dirty]

コミット済みの内容（HEAD）だけを `git archive` で固めます。git に入っていないもの（.env、ログ、バックアップ、
node_modules など）は入りません。ファイル名は ops-<コミットの日付>-<コミット>.zip で、中は同じ名前のフォルダー 1 つ。
改行はリポジトリのまま（LF）にします（Windows の core.autocrlf で CRLF に変えない）。
コミットしていない変更があると止まります（zip に入らないため）。--allow-dirty で、それでも作ります。
サーバーでの入れ方は deploy/README.md。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def package_name(commit_date: str, short_sha: str) -> str:
    """ops-YYYYMMDD-<コミット>（zip の名前と、中のフォルダーの名前）"""
    return f"ops-{commit_date}-{short_sha}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the zip to carry to the server (committed files of HEAD only).")
    parser.add_argument("--out", default="packages", help="output folder (default: packages, git-ignored)")
    parser.add_argument("--allow-dirty", action="store_true", help="build even with uncommitted changes (they are not included)")
    args = parser.parse_args()

    changes = git("status", "--porcelain", "--untracked-files=no")
    if changes and not args.allow_dirty:
        print("There are uncommitted changes; they would not be in the zip. Commit them first "
              "(or use --allow-dirty):\n" + changes, file=sys.stderr)
        return 1

    name = package_name(git("show", "-s", "--format=%cd", "--date=format:%Y%m%d", "HEAD"),
                        git("rev-parse", "--short", "HEAD"))
    out_dir = (ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.zip"
    git("-c", "core.autocrlf=false", "archive", "--format=zip", f"--prefix={name}/", "-o", str(out), "HEAD")

    print(f"Created {out} ({out.stat().st_size / 1024:.0f} KB, branch {git('rev-parse', '--abbrev-ref', 'HEAD')})")
    print("On the server:")
    print(f"  sudo unzip {name}.zip -d /srv/ops/releases")
    print(f"  sudo sh /srv/ops/releases/{name}/deploy/install.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())

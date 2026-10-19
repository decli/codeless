"""工作区：项目代码所在目录，用 git 做检查点。git 在编排侧执行，沙箱里看不到任何凭证。"""

from __future__ import annotations

import difflib
import io
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

# 浏览文件、搜索、打包时跳过的目录
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".venv", "venv", "dist", "build", ".next", ".cache", ".codeless", ".turbo", "coverage",
}
BASE_GITIGNORE = ["node_modules/", "__pycache__/", "*.pyc", ".venv/", "dist/", ".codeless/", ".DS_Store"]
MAX_FILE_BYTES = 2_000_000
# 检查点编号：提交哈希，可带一个 ^ 表示它的上一个（撤销本轮用）。不能以 - 开头，防止被 git 当成选项
REV = re.compile(r"^[0-9a-f]{4,40}\^?$")


def git(path: Path, *args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = [
        "git", "-C", str(path),
        "-c", "user.name=codeless",
        "-c", "user.email=agent@codeless.local",
        "-c", "safe.directory=*",
        "-c", "core.quotepath=false",
        *args,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} 失败：{proc.stderr.strip()[:300]}")
    return proc


def init_workspace(path: Path, starter: Path | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if starter and starter.is_dir():
        shutil.copytree(
            starter, path, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("node_modules", "dist", "__pycache__"),
        )
    ignore = path / ".gitignore"
    existing = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    missing = [line for line in BASE_GITIGNORE if line not in existing]
    if missing:
        ignore.write_text("\n".join([*existing, *missing]).strip() + "\n", encoding="utf-8")
    git(path, "init", "-q", "-b", "main")
    git(path, "add", "-A")
    git(path, "commit", "-q", "--allow-empty", "-m", "初始化项目")


def head(path: Path) -> str:
    return git(path, "rev-parse", "HEAD", check=False).stdout.strip()


def _numstat(path: Path, base: str, target: str) -> list[dict]:
    files = []
    proc = git(path, "diff", "--numstat", "-M", base, target, check=False)
    status = git(path, "diff", "--name-status", "-M", base, target, check=False)
    kinds = {}
    for line in status.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            kinds[parts[-1]] = parts[0][0]
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add, delete, name = parts[0], parts[1], parts[-1]
        if " => " in name:
            name = name.split(" => ")[-1].rstrip("}")
        files.append(
            {
                "path": name,
                "additions": int(add) if add.isdigit() else 0,
                "deletions": int(delete) if delete.isdigit() else 0,
                "status": {"A": "added", "D": "deleted", "R": "renamed"}.get(kinds.get(name, "M"), "modified"),
                "binary": add == "-",
            }
        )
    return files


def commit_all(path: Path, message: str) -> dict | None:
    """有改动就提交一个检查点，返回改动统计；没改动返回 None。"""
    if not (path / ".git").exists():
        return None
    before = head(path)
    git(path, "add", "-A")
    if not git(path, "status", "--porcelain", check=False).stdout.strip():
        return None
    git(path, "commit", "-q", "-m", message)
    sha = head(path)
    files = _numstat(path, before, sha) if before else []
    return {
        "sha": sha,
        "message": message,
        "files": files,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }


def check_rev(sha: str) -> str:
    if not REV.match(sha or ""):
        raise ValueError("检查点编号不对")
    return sha


def restore(path: Path, sha: str, message: str) -> dict | None:
    """把工作区恢复到某个检查点，作为一个新提交，历史保留，可以再回滚回来。"""
    check_rev(sha)
    git(path, "cat-file", "-e", f"{sha}^{{commit}}")
    git(path, "add", "-A")
    git(path, "read-tree", "-u", "--reset", sha)
    return commit_all(path, message)


def checkpoints(path: Path, limit: int = 50) -> list[dict]:
    proc = git(path, "log", f"-{limit}", "--format=%H%x1f%s%x1f%ct", check=False)
    out = []
    for line in proc.stdout.splitlines():
        sha, subject, ts = (line.split("\x1f") + ["", "", "0"])[:3]
        out.append({"sha": sha, "message": subject, "time": int(ts or 0)})
    return out


def file_diff(path: Path, sha: str, rel: str) -> str:
    proc = git(path, "show", "--format=", "-M", check_rev(sha), "--", rel, check=False)
    return proc.stdout


def text_diff(old: str, new: str, rel: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3,
    )
    return "".join(lines)


def safe_path(root: Path, rel: str) -> Path:
    """把模型或用户给的相对路径限制在工作区内，兼容写成 /workspace/xxx 的绝对路径。"""
    rel = (rel or ".").strip()
    if rel.startswith("/workspace"):
        rel = rel[len("/workspace"):].lstrip("/") or "."
    root = root.resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"路径越界，只能访问项目目录内的文件：{rel}")
    return target


def walk(root: Path, base: Path | None = None, max_entries: int = 2000) -> list[dict]:
    """列出工作区文件（跳过依赖和构建目录），用于文件树。"""
    base = base or root
    out: list[dict] = []
    stack = [base]
    while stack and len(out) < max_entries:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            continue
        dirs = []
        for entry in entries:
            if entry.name in SKIP_DIRS or entry.is_symlink():
                continue
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                out.append({"path": rel, "type": "dir"})
                dirs.append(entry)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                out.append({"path": rel, "type": "file", "size": size})
            if len(out) >= max_entries:
                break
        stack.extend(reversed(dirs))
    return sorted(out, key=lambda e: e["path"])


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


def read_text(root: Path, rel: str, max_bytes: int = MAX_FILE_BYTES) -> tuple[str | None, int]:
    target = safe_path(root, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    size = target.stat().st_size
    with target.open("rb") as fh:
        data = fh.read(max_bytes)
    if is_binary(data):
        return None, size
    return data.decode("utf-8", errors="replace"), size


def zip_workspace(root: Path) -> bytes:
    """打包源码：只包含 git 跟踪的和未被忽略的文件，不带 node_modules 之类。"""
    proc = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", check=False)
    names = [n for n in proc.stdout.split("\0") if n]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            file = root / name
            if file.is_file():
                zf.write(file, name)
    return buffer.getvalue()

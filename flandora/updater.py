"""从 GitHub Release 下载新版、暂存、并在退出后替换当前安装。

原则：
- 只在**打包安装**（PyInstaller 冻结）时有意义；源码模式更新等于 `git pull`，这里如实告知而不动手。
- 下载与解压都在 `data/updates/` 里做，不碰正在运行的程序目录；替换动作交给一个等本进程退出后
  才执行的 PowerShell 脚本，失败就回滚到备份目录。
- 任何一步失败都抛 `UpdateError` 并带上人能看懂的原因，不静默降级。
"""

import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile

from . import __version__, config

REPO = "OMSociety/Flandora"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
ASSET_RE = re.compile(r'href="(/[^"]*/releases/download/[^"]+\.zip)"')
TIMEOUT = 20.0

_opener = urllib.request.urlopen


class UpdateError(Exception):
    """更新流程中的可预期失败，消息直接展示给用户。"""


def updates_dir() -> str:
    path = os.path.join(config.data_dir(), "updates")
    os.makedirs(path, exist_ok=True)
    return path


def pending_file() -> str:
    return os.path.join(updates_dir(), "pending.json")


def install_mode() -> str:
    """`frozen`：打包安装（能自我替换）；`source`：源码运行（更新走 git pull）。"""
    return "frozen" if getattr(sys, "frozen", False) else "source"


def app_dir() -> str:
    """程序所在目录：冻结时是可执行文件目录，源码时是仓库根。"""
    if install_mode() == "frozen":
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- 查询新版本


def _fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"Flandora/{__version__}", "Accept": "text/html"}
    )
    with _opener(request, timeout=timeout) as response:
        return response.read()


def _version_from_tag(tag: str) -> str:
    return tag.strip().lstrip("vV")


def _asset_for(tag: str) -> str:
    """在发布页的资源列表里找 zip。

    GitHub 的 release 页面资源是异步加载的，但 `expanded_assets/<tag>` 这个片段的 HTML
    里带着真实的下载链接，不需要 JS 也不需要 API 配额。
    """
    page = f"{RELEASES_PAGE}/expanded_assets/{tag}"
    try:
        body = _fetch(page, TIMEOUT).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - 页面结构会变，失败就退到约定命名
        raise UpdateError(f"取不到 {tag} 的资源列表：{type(e).__name__}: {e}") from e
    match = ASSET_RE.search(body)
    if not match:
        # 退路：build.py 的命名约定是 Flandora-v<版本>.zip
        return f"{RELEASES_PAGE}/download/{tag}/Flandora-{tag}.zip"
    return "https://github.com" + match.group(1)


def latest_release(timeout: float = TIMEOUT) -> dict:
    """返回 {"version", "tag", "url", "asset"}；查不到抛 UpdateError。"""
    feed = f"https://github.com/{REPO}/releases.atom"
    try:
        body = _fetch(feed, timeout)
    except Exception as e:  # noqa: BLE001 - 网络问题统一归一成能展示的错
        raise UpdateError(f"查不到最新版本：{type(e).__name__}: {e}") from e
    tags = re.findall(r"<title>([^<]+)</title>", body.decode("utf-8", "replace"))
    # 第一个 <title> 是订阅本身的标题（形如「Release notes from Flandora」），版本在之后
    candidates = [
        t.strip() for t in tags[1:] if t.strip()[:1] in ("v", "V") or t.strip()[:1].isdigit()
    ]
    if not candidates:
        raise UpdateError("发布订阅里没有版本条目")
    tag = candidates[0]
    version = _version_from_tag(tag)
    return {
        "version": version,
        "tag": tag,
        "url": f"{RELEASES_PAGE}/tag/{tag}",
        "asset": _asset_for(tag),
    }


# ---------------------------------------------------------------- 下载与暂存


def download(asset_url: str, dest_path: str, progress=None, timeout: float = TIMEOUT * 3) -> str:
    """下载到 `dest_path.part` 再改名，避免半截文件被当成完整包。"""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    part = dest_path + ".part"
    request = urllib.request.Request(asset_url, headers={"User-Agent": f"Flandora/{__version__}"})
    total = 0
    try:
        with _opener(request, timeout=timeout) as response:
            length = int(response.headers.get("Content-Length") or 0)
            with open(part, "wb") as fh:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise UpdateError("安装包超过 512 MB，拒绝下载")
                    fh.write(chunk)
                    if progress:
                        progress(total, length)
    except UpdateError:
        _remove_quietly(part)
        raise
    except Exception as e:  # noqa: BLE001 - 下载失败的各种网络异常统一归一
        _remove_quietly(part)
        raise UpdateError(f"下载失败：{type(e).__name__}: {e}") from e
    if total == 0:
        _remove_quietly(part)
        raise UpdateError("下载到 0 字节，安装包地址可能已经失效")
    os.replace(part, dest_path)
    return dest_path


def safe_members(archive: zipfile.ZipFile) -> list[str]:
    """列出条目并拒绝越界路径（zip 里的 `..` 与绝对路径）。"""
    names = []
    for name in archive.namelist():
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise UpdateError(f"安装包里有越界路径，拒绝解压：{name}")
        names.append(name)
    return names


def extract(archive_path: str, target_dir: str) -> str:
    """解压到 `target_dir`，返回真正含程序文件的目录。"""
    os.makedirs(target_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            broken = archive.testzip()
            if broken:
                raise UpdateError(f"安装包损坏（{broken} 校验不过），请重新下载")
            safe_members(archive)
            archive.extractall(target_dir)
    except zipfile.BadZipFile as e:
        raise UpdateError(f"不是有效的 zip：{e}") from e

    for root, _dirs, files in os.walk(target_dir):
        if "Flandora.exe" in files or "Flandora.pyz" in files:
            return root
        if "run_bridge.py" in files:
            return root
    raise UpdateError("安装包里没有找到 Flandora.exe，可能下载到了错误的资源")


def stage(archive_path: str, version: str) -> dict:
    """解压到暂存目录并登记为待安装。返回登记信息。"""
    staging = os.path.join(updates_dir(), f"staging-{version}")
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    payload_root = extract(archive_path, staging)
    info = {
        "version": version,
        "staging": payload_root,
        "archive": archive_path,
        "staged_at": int(time.time()),
        "mode": install_mode(),
        "app_dir": app_dir(),
    }
    with open(pending_file(), "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)
    return info


def pending() -> dict | None:
    """读待安装登记；没有或读不出来都返回 None。"""
    path = pending_file()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict) or not info.get("staging"):
        return None
    if not os.path.isdir(info["staging"]):
        return None
    return info


def clear_pending() -> None:
    _remove_quietly(pending_file())


# ---------------------------------------------------------------- 退出后替换


def can_write_app_dir() -> bool:
    """程序目录可写吗（Program Files 下通常不可写，需要管理员运行脚本）。"""
    probe = os.path.join(app_dir(), ".flandora-write-test")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.unlink(probe)
        return True
    except OSError:
        return False


def swap_script(info: dict, backup_dir: str | None = None) -> str:
    """生成「等本进程退出 → 备份旧目录 → 覆盖 → 重启」的 PowerShell 脚本。

    失败时把备份挪回去，保证程序至少还能启动。
    """
    target = info.get("app_dir") or app_dir()
    staging = info["staging"]
    version = info.get("version", "")
    backup = backup_dir or os.path.join(updates_dir(), f"backup-{version or 'old'}")
    exe = os.path.join(target, "Flandora.exe")
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"# 等旧进程退出（最多 60 秒）：{os.getpid()}",
        f"Wait-Process -Id {os.getpid()} -Timeout 60 -ErrorAction SilentlyContinue",
        "Start-Sleep -Milliseconds 800",
        f"$target = '{target}'",
        f"$staging = '{staging}'",
        f"$backup = '{backup}'",
        "if (Test-Path $backup) { Remove-Item $backup -Recurse -Force }",
        "New-Item -ItemType Directory -Path $backup -Force | Out-Null",
        "try {",
        "  Get-ChildItem $target -Force | Where-Object { $_.Name -ne 'data' } | ForEach-Object {",
        "    Move-Item $_.FullName (Join-Path $backup $_.Name) -Force",
        "  }",
        "  Get-ChildItem $staging -Force | ForEach-Object {",
        "    Copy-Item $_.FullName $target -Recurse -Force",
        "  }",
        "  Write-Host '更新完成，正在启动新版本'",
        f"  Start-Process '{exe}'",
        "} catch {",
        "  Write-Host ('更新失败：' + $_.Exception.Message)",
        "  Get-ChildItem $backup -Force | ForEach-Object {",
        "    Copy-Item $_.FullName $target -Recurse -Force",
        "  }",
        "  Write-Host '已回滚到旧版本，备份留在：' $backup",
        "  Pause",
        "}",
    ]
    return "\r\n".join(lines) + "\r\n"


def write_swap_script(info: dict) -> str:
    """把替换脚本写到 data/updates/ 下并返回路径。"""
    version = info.get("version", "new")
    path = os.path.join(updates_dir(), f"apply-{version}.ps1")
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
        fh.write(swap_script(info))
    return path


def _remove_quietly(path: str) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass

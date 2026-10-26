"""从 GitHub Release 下载新版、暂存、并在退出后替换当前安装。

原则：
- 只在**打包安装**（PyInstaller 冻结）时有意义；源码模式更新等于 `git pull`，这里如实告知而不动手。
- 下载与解压都在 `data/updates/` 里做，不碰正在运行的程序目录；替换动作交给一个等本进程退出后
  才执行的 PowerShell 脚本，失败就回滚到备份目录。
- 任何一步失败都抛 `UpdateError` 并带上人能看懂的原因，不静默降级。
"""

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile

from . import __version__, config

log = logging.getLogger("flandora")

REPO = "OMSociety/Flandora"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
ASSET_RE = re.compile(r'href="(/[^"]*/releases/download/[^"]+\.zip)"')
SHA256_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")
TIMEOUT = 20.0

# 拉起新版本后观察它是否还活着的时间窗口：小于这个时间就退出视为「更新后起不来」
STARTUP_GRACE_SECONDS = 20
# 没提供哈希时的兜底校验：PyInstaller 打出来的主程序不可能只有几百 KB
MIN_EXE_BYTES = 1024 * 1024
# 发布包里的桥接主程序：既有「这份包是不是 Flandora」的标记作用，也是替换后要重启的那个
MAIN_EXE = "Flandora-Bridge.exe"

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
        log.warning(f"[更新] 取不到 {tag} 的资源列表: {e}")
        raise UpdateError(f"取不到 {tag} 的资源列表：网络或接口异常，详细原因见运行日志") from e
    match = ASSET_RE.search(body)
    if not match:
        # 退路：build.py 的命名约定是 Flandora-v<版本>.zip
        return f"{RELEASES_PAGE}/download/{tag}/Flandora-{tag}.zip"
    return "https://github.com" + match.group(1)


def asset_sha256(asset_url: str, timeout: float = TIMEOUT) -> str | None:
    """取发布页为安装包附带的 sha256；没有或不合法时返回 None。

    约定是资源旁边放一个同名加 ``.sha256`` 的文本文件（``shasum -a 256`` 的输出格式
    即可）。没有它不算错误：调用方退到「zip 内 Flandora-Bridge.exe 存在且大小合理」，
    并把「未校验哈希」写进日志。这里只做完整性提示，不是可信签名。
    """
    url = asset_url + ".sha256"
    try:
        body = _fetch(url, timeout).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - 没有校验文件是常态，不是失败
        log.info("未取到 %s（%s），本次不做哈希校验", url, type(e).__name__)
        return None
    match = SHA256_RE.search(body)
    if match is None:
        log.info("%s 里没有 sha256，本次不做哈希校验", url)
        return None
    return match.group(1).lower()


def latest_release(timeout: float = TIMEOUT) -> dict:
    """返回 {"version", "tag", "url", "asset", "sha256"}；查不到抛 UpdateError。

    ``sha256`` 是可选字段：发布页没提供时为 None，下载阶段会退到兜底校验。
    """
    feed = f"https://github.com/{REPO}/releases.atom"
    try:
        body = _fetch(feed, timeout)
    except Exception as e:  # noqa: BLE001 - 网络问题统一归一成能展示的错
        log.warning(f"[更新] 查不到最新版本: {e}")
        raise UpdateError("查不到最新版本：网络或接口异常，详细原因见运行日志") from e
    tags = re.findall(r"<title>([^<]+)</title>", body.decode("utf-8", "replace"))
    # 第一个 <title> 是订阅本身的标题（形如「Release notes from Flandora」），版本在之后
    candidates = [
        t.strip() for t in tags[1:] if t.strip()[:1] in ("v", "V") or t.strip()[:1].isdigit()
    ]
    if not candidates:
        raise UpdateError("发布订阅里没有版本条目")
    tag = candidates[0]
    version = _version_from_tag(tag)
    asset = _asset_for(tag)
    return {
        "version": version,
        "tag": tag,
        "url": f"{RELEASES_PAGE}/tag/{tag}",
        "asset": asset,
        "sha256": asset_sha256(asset, timeout),
    }


# ---------------------------------------------------------------- 下载与暂存


def download(
    asset_url: str,
    dest_path: str,
    progress=None,
    timeout: float = TIMEOUT * 3,
    expected_sha256: str | None = None,
) -> str:
    """下载到 `dest_path.part` 再改名，避免半截文件被当成完整包。

    `expected_sha256` 有值就边写边算摘要并比对；不一致直接删掉半截文件报错。
    没有哈希时只记录「未校验」，交给后面的 `extract()` 兜底检查程序文件。
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    part = dest_path + ".part"
    request = urllib.request.Request(asset_url, headers={"User-Agent": f"Flandora/{__version__}"})
    total = 0
    digest = hashlib.sha256()
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
                    digest.update(chunk)
                    fh.write(chunk)
                    if progress:
                        progress(total, length)
    except UpdateError:
        _remove_quietly(part)
        raise
    except Exception as e:  # noqa: BLE001 - 下载失败的各种网络异常统一归一
        _remove_quietly(part)
        log.warning(f"[更新] 下载失败: {e}")
        raise UpdateError("下载失败：网络或接口异常，详细原因见运行日志") from e
    if total == 0:
        _remove_quietly(part)
        raise UpdateError("下载到 0 字节，安装包地址可能已经失效")
    if expected_sha256:
        actual = digest.hexdigest()
        if actual != expected_sha256.lower():
            _remove_quietly(part)
            raise UpdateError(f"安装包 sha256 不一致：期望 {expected_sha256}，实际 {actual}")
        log.info("安装包 sha256 校验通过：%s", actual)
    else:
        log.info("发布页没有提供 sha256，跳过哈希校验，改为核对 zip 内容与文件大小")
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
        log.warning(f"[更新] 解压失败: {e}")
        raise UpdateError("下载到的文件不是有效的 zip（可能没下完），详细原因见运行日志") from e

    for root, _dirs, files in os.walk(target_dir):
        if MAIN_EXE in files:
            _check_exe_size(os.path.join(root, MAIN_EXE))
            return root
        if "Flandora.pyz" in files or "run_bridge.py" in files:
            return root
    raise UpdateError(f"安装包里没有找到 {MAIN_EXE}，可能下载到了错误的资源")


def _check_exe_size(exe_path: str) -> None:
    """没有哈希可校时，用主程序体积挡掉损坏或占位包。

    PyInstaller 打出来的主程序通常在 10 MB 量级；不到 1 MiB 只可能是占位
    文件、被截断的包或错误资源，装上就会把能用的程序目录换成跑不起来的东西。
    """
    try:
        size = os.path.getsize(exe_path)
    except OSError as e:
        log.warning(f"[更新] 读不到安装包里的 {MAIN_EXE}: {e}")
        raise UpdateError(f"读不到安装包里的 {MAIN_EXE}（解压失败），详细原因见运行日志") from e
    if size < MIN_EXE_BYTES:
        raise UpdateError(f"安装包里的 {MAIN_EXE} 只有 {size} 字节，明显不完整，拒绝安装")


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


def _ps_literal(value: str) -> str:
    """把路径写成 PowerShell 双引号字面量，转义反引号、引号与美元符。

    路径按 PowerShell 单引号字符串拼：目录名里一旦有单引号，直接拼接就是语法错误，更新停在
    半途。双引号串的转义序列固定（`````、```"``、```$``），任何路径都能安全嵌入。
    """
    escaped = value.replace("`", "``").replace('"', '`"').replace("$", "`$")
    return f'"{escaped}"'


def swap_script(info: dict, backup_dir: str | None = None) -> str:
    """生成「等本进程退出 → 备份程序目录 → 覆盖 → 重启并观察」的 PowerShell 脚本。

    失败时把备份挪回去，保证程序至少还能启动。新版本拉起后观察
    ``STARTUP_GRACE_SECONDS`` 秒；进程提前退出同样算失败并回滚——「更新成功但起不来」
    比不更新更糟，用户至少还能回到备份里那份能跑的版本。
    """
    target = info.get("app_dir") or app_dir()
    staging = info["staging"]
    version = info.get("version", "")
    backup = backup_dir or os.path.join(updates_dir(), f"backup-{version or 'old'}")
    exe = os.path.join(target, MAIN_EXE)
    lines = [
        '$ErrorActionPreference = "Stop"',
        f"# 本进程 {os.getpid()} 退出后再动程序目录，最多等 60 秒",
        f"Wait-Process -Id {os.getpid()} -Timeout 60 -ErrorAction SilentlyContinue",
        "Start-Sleep -Milliseconds 800",
        f"$target = {_ps_literal(target)}",
        f"$staging = {_ps_literal(staging)}",
        f"$backup = {_ps_literal(backup)}",
        f"$exe = {_ps_literal(exe)}",
        f"$grace = {STARTUP_GRACE_SECONDS}",
        f"# 本次更新到 {version or '未知版本'}",
        "if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }",
        "New-Item -ItemType Directory -Path $backup -Force | Out-Null",
        "try {",
        "  Get-ChildItem -LiteralPath $target -Force | Where-Object { $_.Name -ne 'data' } | ForEach-Object {",
        "    Move-Item -LiteralPath $_.FullName -Destination (Join-Path $backup $_.Name) -Force",
        "  }",
        "  Get-ChildItem -LiteralPath $staging -Force | ForEach-Object {",
        "    Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse -Force",
        "  }",
        "  $proc = Start-Process -FilePath $exe -PassThru",
        "  $deadline = (Get-Date).AddSeconds($grace)",
        "  $alive = $true",
        "  while ((Get-Date) -lt $deadline) {",
        "    Start-Sleep -Milliseconds 500",
        "    $live = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue",
        "    if ($null -eq $live -or $live.HasExited) { $alive = $false; break }",
        "  }",
        '  if (-not $alive) { throw "新版本启动后 $grace 秒内退出（进程 $($proc.Id) 已不在）" }',
        "  Write-Host '更新完成，新版本正在运行'",
        "} catch {",
        "  Write-Host ('更新失败：' + $_.Exception.Message)",
        "  # 回滚：先清掉新版本铺进来的文件（data 目录不动），再把备份覆盖回去。",
        "  # 备份里一定有原版本的每个文件，不清就会留下新版本多出来的那几个。",
        "  Get-ChildItem -LiteralPath $target -Force | Where-Object { $_.Name -ne 'data' } | Remove-Item -Recurse -Force",
        "  Get-ChildItem -LiteralPath $backup -Force | ForEach-Object {",
        "    Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse -Force",
        "  }",
        "  Write-Host ('已回滚到原版本，备份留在：' + $backup)",
        "  Pause",
        "}",
    ]
    return "\r\n".join(lines) + "\r\n"


def write_swap_script(info: dict) -> str:
    """把替换脚本写到 data/updates/ 下并返回路径。

    脚本文本里已经是 CRLF，写盘时关掉换行翻译（``newline=""``），否则会再插一个
    CR，变成 ``\\r\\r\\n``。
    """
    version = info.get("version", "new")
    path = os.path.join(updates_dir(), f"apply-{version}.ps1")
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(swap_script(info))
    log.info("更新脚本已写入 %s", path)
    return path


def _remove_quietly(path: str) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass

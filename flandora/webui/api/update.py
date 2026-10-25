"""更新组：查新、下载暂存、拉起替换脚本、放弃待安装包。

面板只负责编排：真正的网络与装卸在 ``flandora.update_check`` 与
``flandora.updater``。两条动作会改变用户机器（下载安装包、替换程序目录），
登记为 ``defer``，原因见 ``DEFER_REASONS``。
"""

import asyncio
import logging
import os
import threading

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ... import __version__
from ...runtime import RUNTIME

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/update/status": {"GET": "call"},
    "/api/update/check": {"POST": "call"},
    "/api/update/download": {"POST": "defer"},
    "/api/update/install": {"POST": "defer"},
    "/api/update/cancel": {"POST": "call"},
}

DEFER_REASONS: dict[str, str] = {
    "POST /api/update/download": "会真的从 GitHub 下载安装包并解压到 data/updates",
    "POST /api/update/install": "会拉起替换脚本并结束本进程",
}


@router.get("/api/update/status")
def api_update_status():
    """面板要的更新状态：当前版本、安装形态、待安装包、上次查到的版本。"""
    from ... import updater

    return {
        "ok": True,
        "mode": updater.install_mode(),
        "current": __version__,
        "app_dir": updater.app_dir(),
        "app_dir_writable": updater.can_write_app_dir(),
        "latest": RUNTIME.update_available,
        "pending": updater.pending(),
    }


@router.post("/api/update/check")
def api_update_check():
    """现查一次 GitHub 发布页。查不到时把原因原样给出来，不假装是最新。"""
    from ... import update_check, updater

    try:
        info = updater.latest_release()
    except updater.UpdateError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 502)
    newer = update_check.is_newer(info["version"], __version__)
    return {"ok": True, "latest": info, "newer": newer, "current": __version__}


def _download_and_stage() -> dict:
    from ... import update_check, updater

    info = updater.latest_release()
    if not update_check.is_newer(info["version"], __version__):
        raise updater.UpdateError(f"当前已经是 {__version__}，没有更新的版本")
    archive = os.path.join(updater.updates_dir(), f"Flandora-v{info['version']}.zip")
    updater.download(info["asset"], archive, expected_sha256=info.get("sha256"))
    return updater.stage(archive, info["version"])


@router.post("/api/update/download")
async def api_update_download():
    """下载并解压到 data/updates，登记为待安装；不碰正在运行的程序目录。"""
    from ... import updater

    try:
        info = await asyncio.to_thread(_download_and_stage)
    except updater.UpdateError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 502)
    return {"ok": True, "pending": info}


@router.post("/api/update/install")
async def api_update_install():
    """写好替换脚本、拉起它，然后退出本进程让脚本接手。"""
    import subprocess

    from ... import updater

    info = updater.pending()
    if not info:
        return JSONResponse({"ok": False, "error": "还没有下载好的安装包"}, 400)
    if updater.install_mode() != "frozen":
        return JSONResponse(
            {
                "ok": False,
                "error": "源码模式没有可替换的安装目录：请在仓库里 git pull 后重装依赖",
            },
            400,
        )
    if not updater.can_write_app_dir():
        return JSONResponse(
            {
                "ok": False,
                "error": "程序目录不可写（常见于 Program Files）：请以管理员身份运行脚本",
            },
            400,
        )
    script = updater.write_swap_script(info)
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
    threading.Timer(1.5, lambda: os._exit(0)).start()
    log.info("[WebUI] 已拉起更新脚本，准备退出：%s", script)
    return {"ok": True, "script": script, "message": "正在退出并替换，完成后会自动重启"}


@router.post("/api/update/cancel")
def api_update_cancel():
    """放弃待安装的包（暂存目录与登记一起删掉）。"""
    import shutil

    from ... import updater

    info = updater.pending()
    if info and info.get("staging"):
        shutil.rmtree(info["staging"], ignore_errors=True)
    updater.clear_pending()
    return {"ok": True}

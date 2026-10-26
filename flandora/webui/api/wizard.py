"""取钥向导组：四步流程（免责声明 → 数据库口令 → 图片密钥 → 校验）。

状态机与真正的取钥动作都在 ``flandora.wizard``（底层是 ``flandora.wxkey``），本组
只把每一步转成 HTTP。会启动注入/扫描线程或改动已存密钥的四条登记为 ``defer``，
原因见 ``DEFER_REASONS``；查询与取消登记为 ``call``。
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/wizard/status": {"GET": "call"},
    "/api/wizard/disclaimer": {"POST": "call"},
    "/api/wizard/dbkey/start": {"POST": "defer"},
    "/api/wizard/dbkey/cancel": {"POST": "call"},
    "/api/wizard/dbkey/manual": {"POST": "defer"},
    "/api/wizard/imagekey/start": {"POST": "defer"},
    "/api/wizard/imagekey/cancel": {"POST": "call"},
    "/api/wizard/verify": {"POST": "call"},
    "/api/wizard/reset": {"POST": "defer"},
}

DEFER_REASONS: dict[str, str] = {
    "POST /api/wizard/dbkey/start": "会启动取钥器并向微信进程注入",
    "POST /api/wizard/dbkey/manual": "会拿给定口令去解真实库",
    "POST /api/wizard/imagekey/start": "会启动扫描微信内存的取图钥线程",
    "POST /api/wizard/reset": "会取消在跑的取钥任务并重置向导步骤（不删已存口令与密钥文件）",
}


def _wizard_or_error():
    """取向导单例；模块缺失或不满足前置条件时返回 (None, 错误响应)。"""
    try:
        from ... import wizard as wizard_mod
    except ImportError as e:
        log.error(f"[WebUI] 取钥向导模块不可用: {e}")
        return None, JSONResponse(
            {"ok": False, "error": "取钥向导模块不可用（缺依赖或导入失败），详细原因见运行日志"},
            500,
        )
    return wizard_mod.wizard(), None


@router.get("/api/wizard/status")
def api_wizard_status():
    """向导四步的进度、口令与图片密钥的持有情况。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.status()}
    except Exception as e:
        log.error(f"[WebUI] 读取取钥向导状态失败: {e}")
        return JSONResponse({"ok": False, "error": "读取取钥向导状态失败，详细原因见运行日志"}, 500)


@router.post("/api/wizard/disclaimer")
def api_wizard_disclaimer():
    """确认免责声明。界面上的勾选框不打勾就不该走到这里。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.accept_disclaimer()}
    except Exception as e:
        log.error(f"[WebUI] 确认免责声明失败: {e}")
        return JSONResponse({"ok": False, "error": "确认免责声明失败，详细原因见运行日志"}, 400)


@router.post("/api/wizard/dbkey/start")
def api_wizard_dbkey_start():
    """调起取钥器，等用户在微信里重新登录一次。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.start_dbkey()}
    except Exception as e:
        log.error(f"[WebUI] 启动取数据库口令失败: {e}")
        return JSONResponse({"ok": False, "error": "启动取数据库口令失败，详细原因见运行日志"}, 400)


@router.post("/api/wizard/dbkey/cancel")
def api_wizard_dbkey_cancel():
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    return {"ok": True, **wiz.cancel_dbkey()}


@router.post("/api/wizard/dbkey/manual")
def api_wizard_dbkey_manual(payload: dict):
    """手动填入口令。会先拿当前账号的真库验一遍，过了才落盘。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.save_manual_passphrase((payload or {}).get("passphrase", ""))}
    except Exception as e:
        log.error(f"[WebUI] 手动填入口令失败: {e}")
        return JSONResponse({"ok": False, "error": "手动填入口令失败，详细原因见运行日志"}, 400)


@router.post("/api/wizard/imagekey/start")
def api_wizard_imagekey_start():
    """开始扫内存找图片密钥。跑起来之后要在实况画面里点开一张没看过的图。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.start_imagekey()}
    except Exception as e:
        log.error(f"[WebUI] 启动图片密钥扫描失败: {e}")
        return JSONResponse({"ok": False, "error": "启动图片密钥扫描失败，详细原因见运行日志"}, 400)


@router.post("/api/wizard/imagekey/cancel")
def api_wizard_imagekey_cancel():
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    return {"ok": True, **wiz.cancel_imagekey()}


@router.post("/api/wizard/verify")
def api_wizard_verify():
    """拿当前口令逐个库过页 1 HMAC，结果异步写回状态里。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.verify()}
    except Exception as e:
        log.error(f"[WebUI] 口令校验失败: {e}")
        return JSONResponse({"ok": False, "error": "口令校验失败，详细原因见运行日志"}, 400)


@router.post("/api/wizard/reset")
def api_wizard_reset():
    """把四步清回起点（免责声明的确认保留）。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    return {"ok": True, **wiz.reset()}

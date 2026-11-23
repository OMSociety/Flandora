"""入站文件下载：只服务 ``file_share`` 登记过的路径。

不在 ``/api/`` 下，不走面板登录；访问凭据是登记时生成的随机令牌。
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from ... import file_share

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/files/{token}/{name}": {"GET": "defer"},
}

DEFER_REASONS: dict[str, str] = {
    "GET /files/{token}/{name}": "需要先登记一枚真实令牌与本机文件（tests/test_file_share.py 覆盖）",
}


@router.get("/files/{token}/{name}")
def download(token: str, name: str):
    entry = file_share.lookup(token)
    if entry is None:
        return JSONResponse({"ok": False, "error": "文件不存在或链接已失效"}, 404)
    path, filename = entry
    return FileResponse(path, filename=filename)

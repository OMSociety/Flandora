# -*- coding: utf-8 -*-
"""Flandora 一键打包脚本

打包四个组件：
  - Flandora.exe            来自 run_bridge.py（flandora 包的入口）
  - Flandora-Manager.exe    来自 manager/bridge_manager.py
  - Flandora-LogTail.exe    来自 manager/log_tail.py
  - Flandora-KeyTool.exe    来自 tools/keytool.py（唯一带 frida 的 exe）

前三个是运行必需；取钥器只在首次取钥与换号时用，单独成一个 exe 是为了不让
frida 的几十 MB 拖进主程序。

产物：
  dist/Flandora-v1.0.0.zip              portable 包
  dist/Flandora-v1.0.0-setup.exe        安装程序（需要 Inno Setup）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG_SRC = ROOT / "flandora"
MANAGER_SRC = ROOT / "manager"
TOOLS_SRC = ROOT / "tools"
WEBUI_SRC = PKG_SRC / "webui"
WEBUI_FRONTEND = ROOT / "webui-frontend"
WEBUI_DIST = WEBUI_SRC / "dist"
DOCS_SRC = ROOT / "docs"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PYTHON = Path(sys.executable)

VERSION = "1.0.0"
PKG_NAME = f"Flandora-v{VERSION}"

# pip 走清华源，国内直连 PyPI 常常超时
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

EXE_BRIDGE = "Flandora"
EXE_MANAGER = "Flandora-Manager"
EXE_LOGTAIL = "Flandora-LogTail"
EXE_KEYTOOL = "Flandora-KeyTool"

EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def clean() -> None:
    for p in [DIST, BUILD]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for sub in [PKG_SRC, MANAGER_SRC, TOOLS_SRC]:
        for d in sub.rglob("__pycache__"):
            shutil.rmtree(d, ignore_errors=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(" ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None)


def install_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        print(f"PyInstaller 已安装：{PyInstaller.__version__}")
    except ImportError:
        step("安装 PyInstaller")
        run([str(PYTHON), "-m", "pip", "install", "-i", PIP_MIRROR, "pyinstaller"])


def pyi(src: Path, name: str, workdir: Path, onefile: bool = True,
        windowed: bool = False, extra_args: list[str] | None = None) -> None:
    cmd = [
        str(PYTHON), "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", name,
        "--workpath", str(BUILD / f"work-{name}"),
        "--distpath", str(DIST),
        "--specpath", str(BUILD / "specs"),
    ]
    if onefile:
        cmd.append("--onefile")
    if windowed:
        cmd.append("--windowed")
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(str(src))
    run(cmd, cwd=workdir)


def ensure_webui_dist() -> None:
    """确保 flandora/webui/dist 存在（WebUI 前端静态产物）。

    dist 不在仓库里，必须由 vite 现场构建；缺了它 exe 能跑但 WebUI 只返回
    503「前端尚未构建」。发布包不该缺这一块，所以这里直接构建，失败就中止。
    """
    if (WEBUI_DIST / "index.html").is_file():
        print(f"webui/dist 已存在：{WEBUI_DIST}")
        return
    step("构建 WebUI 前端")
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit(
            "找不到 npm，无法构建 WebUI 前端。\n"
            f"请自行执行：cd {WEBUI_FRONTEND} && npm install && npm run build"
        )
    try:
        if not (WEBUI_FRONTEND / "node_modules").is_dir():
            run([npm, "install"], cwd=WEBUI_FRONTEND)
        run([npm, "run", "build"], cwd=WEBUI_FRONTEND)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"WebUI 前端构建失败（退出码 {exc.returncode}）。\n"
            f"请自行排查：cd {WEBUI_FRONTEND} && npm install && npm run build"
        ) from exc
    if not (WEBUI_DIST / "index.html").is_file():
        raise SystemExit(f"前端构建后仍未生成 {WEBUI_DIST / 'index.html'}，请检查 vite 配置。")


def build_bridge() -> None:
    step(f"打包 {EXE_BRIDGE}{EXE_SUFFIX}")
    extras = [
        # 入口脚本放在仓库根，flandora 才会被完整收集
        "--paths", str(ROOT),
        "--add-data", f"{PKG_SRC / 'config.example.json'};.",
        "--add-data", f"{WEBUI_DIST};flandora/webui/dist",
        # 隐藏 import（PyInstaller 默认按 import 追踪，一些动态加载需要手动加）
        "--hidden-import", "websockets",
        "--hidden-import", "comtypes.gen",
        "--hidden-import", "uiautomation",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "flandora.webui",
        "--hidden-import", "flandora.webui.app",
        "--hidden-import", "flandora.webui.monitor",
        # uvicorn 用字符串动态选择事件循环、HTTP 与 WebSocket 实现，静态追踪不到
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.protocols.websockets.websockets_impl",
        "--hidden-import", "uvicorn.lifespan.on",
    ]
    pyi(ROOT / "run_bridge.py", EXE_BRIDGE, ROOT, onefile=True, windowed=False, extra_args=extras)


def build_keytool() -> None:
    step(f"打包 {EXE_KEYTOOL}{EXE_SUFFIX}")
    extras = [
        "--paths", str(ROOT),
        # frida 的 Python 包带编译扩展与二进制，collect-all 才能拿全
        "--collect-all", "frida",
    ]
    pyi(TOOLS_SRC / "keytool.py", EXE_KEYTOOL, ROOT, onefile=True, windowed=False, extra_args=extras)


def build_log_tail() -> None:
    step(f"打包 {EXE_LOGTAIL}{EXE_SUFFIX}")
    pyi(MANAGER_SRC / "log_tail.py", EXE_LOGTAIL, MANAGER_SRC, onefile=True, windowed=False)


def build_manager() -> None:
    step(f"打包 {EXE_MANAGER}{EXE_SUFFIX}")
    extras = [
        "--hidden-import", "pystray",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "tkinter",
    ]
    pyi(MANAGER_SRC / "bridge_manager.py", EXE_MANAGER, MANAGER_SRC,
        onefile=True, windowed=True, extra_args=extras)


def assemble_zip() -> Path:
    step("组装发布 zip")
    rel = DIST / PKG_NAME
    if rel.exists():
        shutil.rmtree(rel)
    (rel / "data").mkdir(parents=True)

    # 1) exe
    for name in (EXE_BRIDGE, EXE_MANAGER, EXE_LOGTAIL, EXE_KEYTOOL):
        fname = f"{name}{EXE_SUFFIX}"
        src = DIST / fname
        if not src.exists():
            raise SystemExit(f"缺少打包产物 {src}")
        shutil.copy2(src, rel / fname)

    # 2) 可写数据目录：首次启动时程序会在这里生成 config.json
    (rel / "data").mkdir(parents=True, exist_ok=True)

    # 3) 启动脚本（双击启动管理器）
    (rel / "启动.bat").write_text(
        f"""@echo off
chcp 65001 >nul
start "" {EXE_MANAGER}{EXE_SUFFIX}
""",
        encoding="utf-8",
    )

    # 4) README
    readme = ROOT / "README.md"
    if readme.is_file():
        shutil.copy2(readme, rel / "README.md")

    # 5) LICENSE
    if (ROOT / "LICENSE").is_file():
        shutil.copy2(ROOT / "LICENSE", rel / "LICENSE")

    # 6) docs（已知限制与验证记录、第三方许可）
    if DOCS_SRC.is_dir():
        shutil.copytree(DOCS_SRC, rel / "docs")

    # 7) 打包 zip
    zip_path = DIST / f"{PKG_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in rel.rglob("*"):
            if path.is_file():
                arc = path.relative_to(DIST).as_posix()
                zf.write(path, arc)
    print(f"\n生成：{zip_path} ({zip_path.stat().st_size / 1024 / 1024:.2f} MB)")
    return zip_path


def find_iscc() -> Path | None:
    """定位 Inno Setup 的命令行编译器 ISCC.exe。

    依次看：环境变量 ISCC（完整路径）、INNO_SETUP_DIR（安装目录）、PATH，
    最后退到几个常见安装位置。
    """
    direct = os.environ.get("ISCC", "").strip()
    if direct and Path(direct).is_file():
        return Path(direct)
    dirs = [
        os.environ.get("INNO_SETUP_DIR", "").strip(),
        r"C:\Program Files (x86)\Inno Setup 7",
        r"C:\Program Files\Inno Setup 7",
        r"C:\Program Files (x86)\Inno Setup 6",
        r"C:\Program Files\Inno Setup 6",
    ]
    for d in dirs:
        if d and (Path(d) / "ISCC.exe").is_file():
            return Path(d) / "ISCC.exe"
    found = shutil.which("ISCC") or shutil.which("iscc")
    return Path(found) if found else None


def build_installer() -> Path | None:
    """编译 setup exe。没装 Inno Setup 就跳过：zip 本身已是可用产物。"""
    step("编译安装程序（setup exe）")
    iscc = find_iscc()
    if iscc is None:
        print(
            "找不到 Inno Setup 的 ISCC.exe，本次不生成 setup exe。\n"
            "  装好后重跑即可，或设环境变量 ISCC=<ISCC.exe 的完整路径>。\n"
            "  下载地址：https://jrsoftware.org/isdl.php"
        )
        return None
    iss = ROOT / "installer" / "Flandora.iss"
    if not iss.is_file():
        raise SystemExit(f"缺少安装脚本 {iss}")
    run([str(iscc), str(iss)], cwd=iss.parent)
    setup = DIST / f"{PKG_NAME}-setup.exe"
    if not setup.is_file():
        raise SystemExit(f"ISCC 执行完毕但没有生成 {setup}")
    print(f"\n生成：{setup} ({setup.stat().st_size / 1024 / 1024:.2f} MB)")
    return setup


def main() -> int:
    t0 = time.time()
    clean()
    DIST.mkdir(parents=True, exist_ok=True)
    ensure_webui_dist()
    install_pyinstaller()
    build_bridge()
    build_keytool()
    build_log_tail()
    build_manager()
    zip_path = assemble_zip()
    setup_path = build_installer()
    # 这里不要用 emoji：Windows 控制台默认 GBK 编码，编码失败会让打包成功却以退出码 1 结束
    print(f"\n[完成] 打包完成，耗时 {time.time() - t0:.1f}s")
    print(f"   portable：{zip_path}")
    if setup_path is not None:
        print(f"   安装程序：{setup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Flandora 一键打包脚本

打包三个组件：
  - Flandora-Bridge.exe     来自 run_bridge.py（flandora 包的入口）
  - Flandora.exe            来自 manager/main.py（托盘管理器，用户双击的那一个）
  - tools/Flandora-KeyTool.exe  来自 tools/keytool.py（唯一带 frida 的 exe）

前两个是运行必需；取钥器只在首次取钥与换号时用，收进 tools/ 子目录，既不
抢根目录的入口，也不让 frida 的几十 MB 拖进主程序。

产物：
  dist/Flandora-v<版本>.zip              portable 包
  dist/Flandora-v<版本>-setup.exe        安装程序（需要 Inno Setup）

版本号只从 ``flandora/__init__.py`` 的 ``__version__`` 读；安装脚本的版本宏由
``tools/sync_version.py`` 写进 ``installer/version.iss``。用法：

    python build.py              打包（前端产物比源码旧时自动重建）
    python build.py --skip-web   不重建前端，按现有 flandora/webui/dist 打包
    python build.py --check      只校验不打包（版本、随包资源、前端产物、安装脚本引用）
"""

from __future__ import annotations

import argparse
import os
import re
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
ISS = ROOT / "installer" / "Flandora.iss"
VERSION_ISS = ROOT / "installer" / "version.iss"


def _read_version() -> str:
    """从包内读版本号：发布版本的唯一来源（见 ``tools/sync_version.py``）。"""
    source = PKG_SRC / "__init__.py"
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"读不到版本来源 {source}：{e}") from e
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit(f'{source} 里没有 __version__ = "…"')
    return match.group(1)


VERSION = _read_version()
PKG_NAME = f"Flandora-v{VERSION}"

# 随包打进主 exe 的资源：(源路径, exe 内的目录)。--check 与打包共用这张表，
# 所以「声明了却没打进去」和「打进去却没声明」都会被看见。
BRIDGE_DATA: tuple[tuple[Path, str], ...] = (
    (PKG_SRC / "config.example.json", "."),
    (WEBUI_DIST, "flandora/webui/dist"),
)

# 载荷（dist/Flandora-v<版本>）里必须有的条目。这份清单就是安装器实际铺到
# 用户机器上的东西；主 exe 内嵌的 config.example.json 与前端产物不在这里——它们
# 随 exe 分发，往载荷里再放一份没有消费方，只会让 zip 变大。
REQUIRED_PAYLOAD = (
    "Flandora.exe",
    "Flandora-Bridge.exe",
    "tools/Flandora-KeyTool.exe",
    "README.md",
    "LICENSE",
    "docs",
)

# pip 走清华源，国内直连 PyPI 常常超时
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

EXE_BRIDGE = "Flandora-Bridge"
EXE_MANAGER = "Flandora"
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


def pyi(
    src: Path,
    name: str,
    workdir: Path,
    onefile: bool = True,
    windowed: bool = False,
    extra_args: list[str] | None = None,
) -> None:
    cmd = [
        str(PYTHON),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--workpath",
        str(BUILD / f"work-{name}"),
        "--distpath",
        str(DIST),
        "--specpath",
        str(BUILD / "specs"),
    ]
    if onefile:
        cmd.append("--onefile")
    if windowed:
        cmd.append("--windowed")
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(str(src))
    run(cmd, cwd=workdir)


def _stamp(mtime: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))


def _newest_frontend_mtime() -> float:
    """前端源码的最新修改时间：``src`` 整棵树 + 构建入口与依赖清单。

    依赖清单也算进来：升级 vite 或插件后即使没动 ``src``，产物也应该重建。
    """
    newest = 0.0
    for path in (WEBUI_FRONTEND / "src").rglob("*"):
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    entries = [WEBUI_FRONTEND / "index.html", WEBUI_FRONTEND / "package.json"]
    entries.append(WEBUI_FRONTEND / "package-lock.json")
    entries.extend(WEBUI_FRONTEND.glob("vite.config.*"))
    for path in entries:
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    return newest


def ensure_webui_dist(skip_web: bool = False) -> None:
    """确保 ``flandora/webui/dist`` 是最新构建（按 mtime 判断，不是「有文件就跳过」）。

    dist 不在仓库里，必须由 vite 现场构建；缺了它 exe 能跑但 WebUI 只返回
    503「前端尚未构建」。发布包不该缺这一块，所以默认直接构建，失败就中止。

    判据是 ``dist/index.html`` 的 mtime 与前端源码最新 mtime 的比较：产物比源码旧
    就重建。只按「文件在不在」判断会把改完前端没重新构建的旧界面打进安装包。
    ``--skip-web`` 显式跳过重建，但会把两边的 mtime 打出来，便于确认打的是哪一版。
    """
    index = WEBUI_DIST / "index.html"
    newest_src = _newest_frontend_mtime()

    if skip_web:
        if not index.is_file():
            raise SystemExit(
                f"--skip-web 要求 {index} 已经存在；先去掉 --skip-web 跑一次，"
                "或自行执行 cd webui-frontend && npm run build"
            )
        behind = newest_src - index.stat().st_mtime
        verdict = (
            f"产物比源码旧 {int(behind)} 秒，按 --skip-web 原样打包"
            if behind > 0
            else "产物不比源码旧"
        )
        print(
            f"--skip-web：按现有前端产物打包\n"
            f"  dist/index.html       {_stamp(index.stat().st_mtime)}\n"
            f"  最新前端源码          {_stamp(newest_src)}\n"
            f"  判定：{verdict}"
        )
        return

    if index.is_file() and index.stat().st_mtime >= newest_src:
        print(
            f"webui/dist 是最新的：{index}\n"
            f"  dist/index.html       {_stamp(index.stat().st_mtime)}\n"
            f"  最新前端源码          {_stamp(newest_src)}"
        )
        return

    if index.is_file():
        print(
            f"webui/dist 比前端源码旧，重新构建\n"
            f"  dist/index.html       {_stamp(index.stat().st_mtime)}\n"
            f"  最新前端源码          {_stamp(newest_src)}"
        )
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
    if not index.is_file():
        raise SystemExit(f"前端构建后仍未生成 {index}，请检查 vite 配置。")


def bridge_extras() -> list[str]:
    """主 exe 的 PyInstaller 参数：随包资源 + 静态追踪不到的隐藏 import。"""
    extras: list[str] = ["--paths", str(ROOT)]
    for source, target in BRIDGE_DATA:
        extras.extend(["--add-data", f"{source};{target}"])
    extras.extend(
        [
            # 隐藏 import（PyInstaller 默认按 import 追踪，一些动态加载需要手动加）
            "--hidden-import",
            "websockets",
            "--hidden-import",
            "comtypes.gen",
            "--hidden-import",
            "uiautomation",
            "--hidden-import",
            "PIL._tkinter_finder",
            "--hidden-import",
            "flandora.webui",
            "--hidden-import",
            "flandora.webui.app",
            "--hidden-import",
            "flandora.webui.monitor",
            # uvicorn 用字符串动态选择事件循环、HTTP 与 WebSocket 实现，静态追踪不到
            "--hidden-import",
            "uvicorn.logging",
            "--hidden-import",
            "uvicorn.loops.auto",
            "--hidden-import",
            "uvicorn.protocols.http.auto",
            "--hidden-import",
            "uvicorn.protocols.http.h11_impl",
            "--hidden-import",
            "uvicorn.protocols.websockets.auto",
            "--hidden-import",
            "uvicorn.protocols.websockets.websockets_impl",
            "--hidden-import",
            "uvicorn.lifespan.on",
        ]
    )
    return extras


def build_bridge() -> None:
    step(f"打包 {EXE_BRIDGE}{EXE_SUFFIX}")
    pyi(
        ROOT / "run_bridge.py",
        EXE_BRIDGE,
        ROOT,
        onefile=True,
        windowed=False,
        extra_args=bridge_extras(),
    )


def build_keytool() -> None:
    step(f"打包 {EXE_KEYTOOL}{EXE_SUFFIX}")
    extras = [
        "--paths",
        str(ROOT),
        # frida 的 Python 包带编译扩展与二进制，collect-all 才能拿全
        "--collect-all",
        "frida",
    ]
    pyi(
        TOOLS_SRC / "keytool.py", EXE_KEYTOOL, ROOT, onefile=True, windowed=False, extra_args=extras
    )


def build_manager() -> None:
    step(f"打包 {EXE_MANAGER}{EXE_SUFFIX}")
    extras = [
        # 管理器复用 flandora.single_instance 与 flandora.stop_request 两个轻模块；
        # 不把仓库根加进搜索路径，PyInstaller 会把它们当成不存在的模块，打出
        # 一个启动即报 ModuleNotFoundError 的 exe。
        "--paths",
        str(ROOT),
        "--hidden-import",
        "flandora.single_instance",
        "--hidden-import",
        "flandora.stop_request",
    ]
    pyi(
        MANAGER_SRC / "main.py",
        EXE_MANAGER,
        MANAGER_SRC,
        onefile=True,
        windowed=True,
        extra_args=extras,
    )


# ---------------------------------------------------------------- 载荷与自检


def payload_dir() -> Path:
    """安装脚本用的载荷目录：由 ``installer/version.iss`` 的 PayloadDir 决定。"""
    try:
        text = VERSION_ISS.read_text(encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"读不到 {VERSION_ISS}（{e}）：先跑 python tools/sync_version.py") from e
    match = re.search(r'^#define PayloadDir "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit(f"{VERSION_ISS} 里没有 PayloadDir：先跑 python tools/sync_version.py")
    return (ISS.parent / match.group(1).replace("\\", "/")).resolve()


_ISS_SOURCE_RE = re.compile(r'^\s*Source:\s*"\{#PayloadDir\}(?P<rel>[^"]*)"', re.MULTILINE)
_ISS_DEFINE_RE = re.compile(r'^\s*#define\s+(?P<name>\w+)\s+"(?P<value>[^"]*)"', re.MULTILINE)
_ISS_SYMBOL_RE = re.compile(r"\{#(?P<name>\w+)\}")


def installer_defines() -> dict[str, str]:
    """安装脚本里的 ``#define`` 常量（exe 文件名等）。"""
    if not ISS.is_file():
        raise SystemExit(f"缺少安装脚本 {ISS}")
    return {
        m.group("name"): m.group("value")
        for m in _ISS_DEFINE_RE.finditer(ISS.read_text(encoding="utf-8"))
    }


def installer_sources() -> list[str]:
    """安装脚本从载荷里取的文件（相对载荷根；结尾的 ``*`` 表示整个目录）。

    脚本里用 ``{#BridgeExe}`` 这类预处理常量拼路径，这里按 ``#define`` 展开成实际文件名，
    否则自检会把常量本身当成文件去找，报出一串并不存在的缺项。
    """
    if not ISS.is_file():
        raise SystemExit(f"缺少安装脚本 {ISS}")
    defines = installer_defines()
    rels = []
    for match in _ISS_SOURCE_RE.finditer(ISS.read_text(encoding="utf-8")):
        rel = _ISS_SYMBOL_RE.sub(
            lambda m: defines.get(m.group("name"), m.group(0)), match.group("rel")
        )
        unknown = _ISS_SYMBOL_RE.search(rel)
        if unknown is not None:
            raise SystemExit(f"安装脚本里的常量 {unknown.group(0)} 没有 #define，无法核对载荷")
        rels.append(rel.replace("\\", "/").lstrip("/"))
    return rels


def verify_payload(payload: Path) -> list[str]:
    """载荷自检：必需条目与安装脚本引用的文件都在里面；返回问题清单。

    打包流程在生成 zip 之前调用它，缺项就中止——残缺包比没有包更坏：
    用户装上之后才发现少了管理器或文档。
    """
    problems: list[str] = []
    for name in REQUIRED_PAYLOAD:
        path = payload / name
        ok = path.is_dir() if name == "docs" else path.is_file()
        if not ok:
            problems.append(f"载荷里缺 {path}")
    for rel in installer_sources():
        if rel.endswith("*"):
            target = payload / rel[:-1].rstrip("/")
            if not target.is_dir() or not any(target.iterdir()):
                problems.append(f"安装脚本引用的目录不存在或是空的：{target}")
            continue
        target = payload / rel
        if not target.is_file():
            problems.append(f"安装脚本引用的文件不在载荷里：{target}")
    return problems


def assemble_zip() -> Path:
    step("组装发布 zip")
    rel = DIST / PKG_NAME
    if rel.exists():
        shutil.rmtree(rel)
    (rel / "data").mkdir(parents=True)

    # 1) exe：管理器与桥接在根目录，取钥器收进 tools/
    (rel / "tools").mkdir(parents=True, exist_ok=True)
    layout = {
        EXE_BRIDGE: rel,
        EXE_MANAGER: rel,
        EXE_KEYTOOL: rel / "tools",
    }
    for name, target_dir in layout.items():
        fname = f"{name}{EXE_SUFFIX}"
        src = DIST / fname
        if not src.exists():
            raise SystemExit(f"缺少打包产物 {src}")
        shutil.copy2(src, target_dir / fname)

    # 2) 可写数据目录：首次启动时程序会在这里生成 config.json
    (rel / "data").mkdir(parents=True, exist_ok=True)

    # 3) README
    readme = ROOT / "README.md"
    if readme.is_file():
        shutil.copy2(readme, rel / "README.md")

    # 4) LICENSE
    if (ROOT / "LICENSE").is_file():
        shutil.copy2(ROOT / "LICENSE", rel / "LICENSE")

    # 5) docs（已知限制与验证记录、第三方许可）
    if DOCS_SRC.is_dir():
        shutil.copytree(DOCS_SRC, rel / "docs")

    # 6) 自检：缺项就在这里中止，不生成残缺 zip
    problems = verify_payload(rel)
    if problems:
        raise SystemExit("载荷自检不通过，不生成 zip：\n  " + "\n  ".join(problems))
    print(
        f"载荷自检通过：{len(REQUIRED_PAYLOAD)} 个必需条目 + {len(installer_sources())} 条安装脚本引用"
    )

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
    if not ISS.is_file():
        raise SystemExit(f"缺少安装脚本 {ISS}")
    if not VERSION_ISS.is_file():
        raise SystemExit(f"缺少 {VERSION_ISS}：先跑 python tools/sync_version.py")
    run([str(iscc), str(ISS)], cwd=ISS.parent)
    setup = DIST / f"{PKG_NAME}-setup.exe"
    if not setup.is_file():
        raise SystemExit(f"ISCC 执行完毕但没有生成 {setup}")
    print(f"\n生成：{setup} ({setup.stat().st_size / 1024 / 1024:.2f} MB)")
    return setup


def _version_problems() -> list[str]:
    """版本号一致性：复用 tools/sync_version.py 的同一套规则，不在这里再抄一遍口径。"""
    tools_dir = str(ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        import sync_version
    except ImportError as e:
        raise SystemExit(f"找不到 tools/sync_version.py：{e}") from e
    return sync_version.check(str(ROOT))


def check_only() -> int:
    """``--check``：只校验不打包。返回 0 表示全部通过。

    四类校验：版本号各处一致、载荷目录与 build.py 的组装目标一致、
    ``config.example.json`` 在 ``--add-data`` 里、前端产物存在且不比源码旧、
    安装脚本引用的文件都在载荷里（载荷还没生成时跳过并说明）。
    """
    problems: list[str] = []

    print("=== 版本号 ===")
    version_problems = _version_problems()
    if version_problems:
        problems.extend(version_problems)
        for problem in version_problems:
            print(f"  DRIFT  {problem}")
    else:
        print(f"  OK     版本 {VERSION} 与各管理位置一致（来源 flandora/__init__.py）")

    print("\n=== 载荷目录 ===")
    target = payload_dir()
    assembled = (DIST / PKG_NAME).resolve()
    if target != assembled:
        message = (
            f"installer/version.iss 的 PayloadDir 指向 {target}，"
            f"build.py 组装的是 {assembled}（跑 python tools/sync_version.py）"
        )
        problems.append(message)
        print(f"  DRIFT  {message}")
    else:
        print(f"  OK     PayloadDir {target}")

    print("\n=== 随包资源（--add-data）===")
    declared = {source.resolve(): target for source, target in BRIDGE_DATA}
    example = (PKG_SRC / "config.example.json").resolve()
    if example not in declared:
        problems.append(f"config.example.json 没有登记进 BRIDGE_DATA（--add-data）：{example}")
        print(f"  MISSING  {example}")
    elif not example.is_file():
        problems.append(f"config.example.json 不存在：{example}")
        print(f"  MISSING  {example}")
    else:
        print(f"  OK     config.example.json -> {declared[example]}")

    print("\n=== 前端产物 ===")
    index = WEBUI_DIST / "index.html"
    if not index.is_file():
        message = f"缺少前端产物 {index}：先 cd webui-frontend && npm run build"
        problems.append(message)
        print(f"  MISSING  {message}")
    else:
        newest = _newest_frontend_mtime()
        state = "不比源码旧" if index.stat().st_mtime >= newest else "旧于源码，打包时会重建"
        print(f"  OK     {index} {_stamp(index.stat().st_mtime)}（{state}）")

    print("\n=== 安装脚本引用 ===")
    if not target.is_dir():
        print(f"  跳过：{target} 还不存在（先跑一次 python build.py 生成载荷）")
    else:
        payload_problems = verify_payload(target)
        if payload_problems:
            problems.extend(payload_problems)
            for problem in payload_problems:
                print(f"  MISSING  {problem}")
        else:
            print(f"  OK     {len(installer_sources())} 条 Source 引用都在 {target} 里")

    if problems:
        print(f"\n检查不通过：{len(problems)} 项问题", file=sys.stderr)
        return 1
    print("\nbuild.py --check 全部通过")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flandora 打包脚本（版本号只从包内读）")
    parser.add_argument("--check", action="store_true", help="只校验不打包；不通过以非零退出")
    parser.add_argument(
        "--skip-web",
        action="store_true",
        help="跳过前端重建，按现有 flandora/webui/dist 打包（会打印 mtime 依据）",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check_only()

    t0 = time.time()
    clean()
    DIST.mkdir(parents=True, exist_ok=True)
    ensure_webui_dist(skip_web=args.skip_web)
    install_pyinstaller()
    build_bridge()
    build_keytool()
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

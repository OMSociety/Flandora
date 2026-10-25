"""把发布版本号同步到打包、安装与下游包：唯一来源是 ``flandora/__init__.py`` 的 ``__version__``。

发布版本散落在好几处，漏一处就会出现「安装包说 1.0.0、程序自报 1.1.0」。这个脚本
从包内读版本，负责下面这些位置：

    build.py                                 不写字面量，运行期从包内读（本脚本核对这条约定）
    installer/version.iss                    整份生成：AppVersion 与 PayloadDir 两个宏
    sdk/python/flandora_sdk/__init__.py      __version__
    sdk/python/pyproject.toml                [project] version
    mcp/python/flandora_mcp/__init__.py      __version__
    mcp/python/pyproject.toml                [project] version
    README.md                                产物文件名的示例
    docs/third-party-licenses/README.md      产物文件名的示例

除了上面这些位置，脚本还会扫全仓库里「看起来是发布版本」的字面量（``Flandora-v1.2.3``、
``__version__ = "1.2.3"``、pyproject 的 ``version = "1.2.3"``）。出现在管理清单与豁免清单
之外的文件里就算漂移：那意味着又多出一份手工维护的版本号。

用法：

    python tools/sync_version.py           写：把各处对齐到包内版本
    python tools/sync_version.py --check   只比对：漂移即非零退出，逐条打印「文件:行 期望 实际」

发版流程：先改 ``flandora/__init__.py`` 的 ``__version__``，再跑一次本脚本。
CI 跑 ``--check``，漏同步会直接红。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 唯一来源：包内的 __version__
SOURCE_PATH = "flandora/__init__.py"
_SOURCE_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)

# 版本号的形态：至少三段数字，允许预发布后缀（1.2.3-rc1）
_VERSION_CORE = r"\d+\.\d+\.\d+"


@dataclass(frozen=True)
class Rule:
    """「文件里某个位置必须等于发布版本」的一条规则。

    ``pattern`` 必须正好有一个捕获组，捕获的就是版本号本身；写回时只替换捕获到的
    那一段，其余字符原样保留。
    """

    path: str
    pattern: str
    label: str


RULES: tuple[Rule, ...] = (
    Rule(
        "sdk/python/flandora_sdk/__init__.py",
        r'(?m)^__version__ = "(' + _VERSION_CORE + r'[^"]*)"',
        "Python SDK 的 __version__",
    ),
    Rule(
        "sdk/python/pyproject.toml",
        r'(?m)^version = "(' + _VERSION_CORE + r'[^"]*)"',
        "Python SDK 的包版本",
    ),
    Rule(
        "mcp/python/flandora_mcp/__init__.py",
        r'(?m)^__version__ = "(' + _VERSION_CORE + r'[^"]*)"',
        "MCP 服务的 __version__",
    ),
    Rule(
        "mcp/python/pyproject.toml",
        r'(?m)^version = "(' + _VERSION_CORE + r'[^"]*)"',
        "MCP 服务的包版本",
    ),
    Rule("README.md", r"Flandora-v(" + _VERSION_CORE + r")", "README 里的产物文件名"),
    Rule(
        "docs/third-party-licenses/README.md",
        r"Flandora-v(" + _VERSION_CORE + r")",
        "第三方许可说明里的产物文件名",
    ),
)

# 整份生成的文件：内容固定，只有版本是变量。放在这里而不是规则里，
# 是因为它是新文件，没有「旧值」可以就地替换。
VERSION_ISS = "installer/version.iss"
VERSION_ISS_TEXT = """\
; 由 tools/sync_version.py 从 flandora/__init__.py 的 __version__ 生成，不要手改。
; Flandora.iss 用 #include 引这两个宏：安装程序版本与载荷目录。
#define AppVersion "{version}"
#define PayloadDir "..\\dist\\Flandora-v{version}"
"""

# 不许再出现版本字面量的约定：这些文件改为从包内读。
GUARDS: tuple[tuple[str, str, str], ...] = (
    (
        "build.py",
        r"(?m)^VERSION = _read_version\(\)$",
        "build.py 的 VERSION 必须从包内读（VERSION = _read_version()）",
    ),
)

FORBIDDEN: tuple[tuple[str, str, str], ...] = (
    (
        "build.py",
        r'(?m)^VERSION = "',
        "build.py 不许再写版本字面量，用 VERSION = _read_version()",
    ),
)

# 全仓库扫描「又长出一份发布版本」的字面量形态与扫描范围
STRAY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Flandora-v" + _VERSION_CORE), "产物文件名"),
    (re.compile(r'(?m)^__version__ = "' + _VERSION_CORE), "__version__"),
    (re.compile(r'(?m)^version = "' + _VERSION_CORE), "包版本"),
)

SCAN_SUFFIXES = frozenset({".py", ".md", ".toml", ".iss", ".json", ".yml", ".yaml"})
SCAN_SKIP_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".impeccable",
        ".pytest-tmp",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "node_modules",
        "playwright-report",
        "test-results",
        "__pycache__",
        "shots",
    }
)

# 豁免：这些文件里的版本号不是发布版本，不参与同步
EXEMPT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("tests/", "测试夹具里的版本号是构造数据（1.4.0、9.9.9 之类），与发布版本无关"),
    ("webui-frontend/tests/", "前端 e2e 的 /api/status 夹具"),
    ("tools/sync_version.py", "本脚本说明文字里的 1.2.3 是示例"),
)


def _read(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def read_version(root: str = ROOT) -> str:
    """读唯一来源里的版本号；读不到就抛 ``SystemExit``（配置错了要立刻停下）。"""
    try:
        text = _read(root, SOURCE_PATH)
    except OSError as e:
        raise SystemExit(f"读不到版本来源 {SOURCE_PATH}：{e}") from e
    match = _SOURCE_RE.search(text)
    if match is None:
        raise SystemExit(f'{SOURCE_PATH} 里没有 __version__ = "…"')
    return match.group(1)


def _render_version_iss(version: str) -> str:
    return VERSION_ISS_TEXT.format(version=version)


def _managed_paths() -> set[str]:
    paths = {rule.path for rule in RULES}
    paths.add(VERSION_ISS)
    paths.add(SOURCE_PATH)
    # build.py 不在管理清单里：它连字面量都不许有（见 FORBIDDEN），
    # 所以扫描阶段也要覆盖它。
    return paths


def _exempt_reason(rel: str) -> str | None:
    for prefix, reason in EXEMPT_PREFIXES:
        if rel.startswith(prefix):
            return reason
    return None


def check(root: str = ROOT) -> list[str]:
    """比对全部位置，返回问题清单；空清单表示一致。"""
    version = read_version(root)
    problems: list[str] = []

    # 1. 生成的 installer/version.iss 必须逐行等于模板渲染结果
    iss_path = os.path.join(root, VERSION_ISS)
    expected_iss = _render_version_iss(version)
    if not os.path.isfile(iss_path):
        problems.append(
            f"{VERSION_ISS}:1 期望 存在（{expected_iss.splitlines()[-1].strip()}）实际 文件不存在"
        )
    else:
        actual_lines = _read(root, VERSION_ISS).splitlines()
        expected_lines = expected_iss.splitlines()
        for index, expected_line in enumerate(expected_lines, start=1):
            actual_line = actual_lines[index - 1] if index <= len(actual_lines) else ""
            if actual_line != expected_line:
                problems.append(
                    f"{VERSION_ISS}:{index} 期望 {expected_line.strip()} 实际 {actual_line.strip()}"
                )

    # 2. 就地替换的规则文件
    for rule in RULES:
        path = os.path.join(root, rule.path)
        if not os.path.isfile(path):
            problems.append(f"{rule.path}:1 期望 存在 实际 文件不存在（{rule.label}）")
            continue
        text = _read(root, rule.path)
        matches = list(re.finditer(rule.pattern, text))
        if not matches:
            problems.append(
                f"{rule.path}:1 期望 出现 {rule.pattern} 实际 没有匹配行（{rule.label}）"
            )
            continue
        for match in matches:
            actual = match.group(1)
            if actual != version:
                line = _line_at(text, match.start())
                problems.append(f"{rule.path}:{line} 期望 {version} 实际 {actual}（{rule.label}）")

    # 3. 契约：这些文件必须已经改成从包内读
    for path, pattern, reason in GUARDS:
        if not re.search(pattern, _read(root, path)):
            problems.append(f"{path}:1 期望 匹配 {pattern} 实际 没有（{reason}）")
    for path, pattern, reason in FORBIDDEN:
        match = re.search(pattern, _read(root, path))
        if match is not None:
            line = _line_at(_read(root, path), match.start())
            problems.append(f"{path}:{line} 期望 不出现 {pattern} 实际 出现（{reason}）")

    # 4. 全仓库扫描：管理清单与豁免清单之外不许再出现发布版本字面量
    managed = _managed_paths()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SCAN_SKIP_DIRS)
        for name in sorted(filenames):
            if os.path.splitext(name)[1] not in SCAN_SUFFIXES:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel in managed or _exempt_reason(rel) is not None:
                continue
            try:
                text = _read(root, rel)
            except (OSError, UnicodeDecodeError):
                continue
            for pattern, kind in STRAY_PATTERNS:
                match = pattern.search(text)
                if match is None:
                    continue
                line = _line_at(text, match.start())
                problems.append(
                    f"{rel}:{line} 期望 不出现在 {kind} 里 实际 出现 "
                    f"{match.group(0).strip()}（把该文件纳入 RULES/EXEMPT_PREFIXES）"
                )
                break
    return problems


def write(root: str = ROOT) -> list[str]:
    """把各处对齐到包内版本，返回写过的文件（相对路径）。"""
    version = read_version(root)
    written: list[str] = []

    iss_path = os.path.join(root, VERSION_ISS)
    expected_iss = _render_version_iss(version)
    current_iss = _read(root, VERSION_ISS) if os.path.isfile(iss_path) else None
    if current_iss != expected_iss:
        os.makedirs(os.path.dirname(iss_path), exist_ok=True)
        with open(iss_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(expected_iss)
        written.append(VERSION_ISS)

    for rule in RULES:
        text = _read(root, rule.path)
        replaced = rule.pattern

        def substitute(match: re.Match[str]) -> str:
            return match.group(0).replace(match.group(1), version)

        new_text = re.sub(replaced, substitute, text)
        if new_text != text:
            with open(os.path.join(root, rule.path), "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)
            written.append(rule.path)

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把发布版本号同步到打包、安装与下游包（唯一来源：flandora/__init__.py）"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比对不写；有漂移以非零退出并逐条打印「文件:行 期望 实际」",
    )
    args = parser.parse_args(argv)

    version = read_version()
    print(f"版本来源：{SOURCE_PATH} __version__ = {version}")

    if args.check:
        problems = check()
        if not problems:
            print(f"{len(RULES)} 条规则、{len(GUARDS) + 1} 个生成/契约位置全部一致")
            return 0
        print(f"\n版本号漂移 {len(problems)} 处：", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\n修法：改 flandora/__init__.py 的 __version__，再跑 python tools/sync_version.py",
            file=sys.stderr,
        )
        return 1

    written = write()
    if not written:
        print("无需改动：各处已与包内版本一致")
        return 0
    for rel in written:
        print(f"已写入 {rel}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())

"""安装脚本与载荷布局的一致性。

安装脚本用 ``{#BridgeExe}`` 这类 Inno 预处理常量拼路径。这里核对常量能展开成真实
文件名，展开后的每一项都在载荷清单里，三个 exe 的落点与发布说明一致——打包自检与
安装器因此看的是同一份事实，常量写错或漏放文件都会在这里先红。
"""

from __future__ import annotations

import build


def test_installer_sources_expand_to_real_filenames():
    sources = build.installer_sources()
    assert sources
    assert all("{" not in source for source in sources)


def test_installer_sources_match_the_payload_list():
    sources = build.installer_sources()
    files = {source for source in sources if not source.endswith("*")}
    dirs = {source[:-2] for source in sources if source.endswith("/*")}
    for name in build.REQUIRED_PAYLOAD:
        if name == "docs":
            assert name in dirs
        else:
            assert name in files


def test_entry_exe_and_bridge_sit_in_the_root_and_keytool_in_tools():
    sources = build.installer_sources()
    assert "Flandora.exe" in sources
    assert "Flandora-Bridge.exe" in sources
    assert "tools/Flandora-KeyTool.exe" in sources

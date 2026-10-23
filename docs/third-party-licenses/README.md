# 随包分发的第三方组件与许可

`WeBot-Bridge-v1.0.0.zip` 里的可执行文件由 PyInstaller 打包，内含下列第三方组件。
每个组件的许可证原文都在本目录下同名文件里，以文件内容为准；下表是索引，不是许可条款。

## 运行时组件

| 组件 | 版本 | 许可证 | 许可文件 |
|---|---|---|---|
| frida | 17.24.3 | wxWindows Library Licence 3.1 | `frida.txt` |
| pystray | 0.19.5 | LGPL-3.0 | `pystray-LGPL-3.0.txt` |
| Pillow | 13.0.0 | HPND（Python Imaging Library 许可） | `pillow.txt` |
| uiautomation | 2.0.29 | Apache-2.0 | `uiautomation.txt` |
| comtypes | 1.4.17 | MIT | `comtypes.txt` |
| pywin32-ctypes | 0.2.3 | BSD | `pywin32_ctypes.txt` |
| FastAPI | 0.161.1 | MIT | `fastapi.txt` |
| Starlette | 1.9.0 | BSD-3-Clause | `starlette.md` |
| pydantic | 2.14.1 | MIT | `pydantic.txt` |
| pydantic-core | 2.50.1 | MIT | `pydantic_core.txt` |
| annotated-types | 0.8.0 | MIT | `annotated_types.txt` |
| annotated-doc | 0.0.5 | MIT | `annotated_doc.txt` |
| anyio | 4.15.1 | MIT | `anyio.txt` |
| idna | 3.20 | BSD-3-Clause | `idna.md` |
| typing-extensions | 4.16.0 | PSF-2.0 | `typing_extensions.txt` |
| typing-inspection | 0.4.4 | MIT | `typing_inspection.txt` |
| click | 8.5.0 | BSD-3-Clause | `click.txt` |
| h11 | 0.16.0 | MIT | `h11.txt` |
| uvicorn | 0.54.0 | BSD-3-Clause | `uvicorn.md` |
| websockets | 17.3 | BSD-3-Clause | `websockets.txt` |
| requests | 2.34.2 | Apache-2.0 | `requests.txt`、`requests-NOTICE.txt` |
| urllib3 | 2.8.0 | MIT | `urllib3.txt` |
| certifi | 2026.10.23 | MPL-2.0 | `certifi.txt` |
| charset-normalizer | 3.5.2 | MIT | `charset_normalizer.txt` |
| zstandard | 0.25.0 | BSD-3-Clause | `zstandard.txt` |
| opentelemetry-api | 1.45.1 | Apache-2.0 | `opentelemetry_api.txt` |
| six | 1.17.0 | MIT | `six.txt` |

## 打包链组件

这四个组件是 PyInstaller 的引导程序与钩子，被嵌进每个 exe 里，随包一并分发。

| 组件 | 版本 | 许可证 | 许可文件 |
|---|---|---|---|
| PyInstaller | 6.22.3 | GPL-2.0-or-later，附允许用它打包并分发闭源程序的例外条款 | `pyinstaller.txt` |
| pyinstaller-hooks-contrib | 2026.9 | GPL-2.0-or-later，附同样的例外条款 | `pyinstaller_hooks_contrib.txt` |
| altgraph | 0.17.5 | MIT | `altgraph.txt` |
| pefile | 2024.8.26 | MIT | `pefile.txt` |
| packaging | 26.3 | Apache-2.0 或 BSD-2-Clause 双许可 | `packaging.txt` |
| setuptools | 84.2.1 | MIT | `setuptools.txt` |

## 说明

- frida 只随 `WeBotBridge-KeyTool.exe` 分发；主程序不含它。
- pystray 与 Pillow 只随桌面管理器 `WeBotBridge-Manager.exe` 分发。
- 若你要基于本仓库重新打包分发，请自行确认上述条款对你的分发方式是否满足，尤其是
  LGPL-3.0（pystray）对「允许使用者替换该库」的要求。

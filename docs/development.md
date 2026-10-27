# 开发与验收

需要 Python 3.13 与 Node.js（构建前端时）。以下命令在仓库根执行，`python` 指 3.13 的解释器。

## 环境与命令

```powershell
# 装依赖（运行期 + 测试 + ruff + 依赖漏洞扫描，一份清单装全）
python -m pip install -r requirements-dev.txt

# 语法检查、lint、后端测试、测试收集闸
python -m compileall -q flandora manager sdk mcp tools
python -m ruff check .
python -m ruff format --check .
python -m pytest tests -q
python tools/check_test_collection.py

# 版本号单一来源与打包前校验（CI 同款）
python tools/sync_version.py --check
python build.py --check

# SDK（可选：在自己的项目里用它当客户端）
python -m pip install -e sdk\python

# MCP 服务（可选：给 AI 客户端用）
python -m pip install -e mcp\python

# 从源码起桥接
python run_bridge.py

# 从源码起托盘管理器（会顺带拉起桥接）
python manager/main.py
```

这些命令与 CI（[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)）同源，提交前本地跑绿即可。CI 另外会重新生成一次 `sdk/python/flandora_sdk/_catalog.json` 并核对无差异（动作目录改了没同步快照会红）；`package` job 会真的跑一遍 `build.py` 并断言载荷清单，发行链接不必等到发版当天才发现打包断了。

前端在 `webui-frontend/`（Vue 3 + Vite）：

```powershell
cd webui-frontend
npm install
npm test        # Playwright，自动在 4175 起 vite
npm run build   # 产物写入 flandora\webui\dist
```

打包发行版：

```powershell
python build.py
```

产物在 `dist\`：`Flandora-v<版本>.zip` 与 `Flandora-v<版本>-setup.exe`，载荷目录里根目录是 `Flandora.exe`（托盘管理器，用户双击的入口）与 `Flandora-Bridge.exe`（桥接本体），取钥器与表情解码用的 FFmpeg 在 `tools\`。安装程序由 [Inno Setup](https://jrsoftware.org/isdl.php) 编译（界面语言用 `installer\ChineseSimplified.isl`，随版本库提供），没装就只出便携包（可用环境变量 `ISCC` 指向 `ISCC.exe` 的完整路径）。

载荷根有 `build-info.json`：版本、提交、是否会脏、构建时间，以及每个随包文件的 sha256。解压后或安装后都能查出这个包对应哪个提交，`python tools/smoke.py` 拿它核对提交与哈希。

> **提示：** 打包会准备表情解码用的 FFmpeg（官方 LGPL 共享构建）：第一次从 `build.py`
> 里的 `FFMPEG_URL` 下载到 `build\ffmpeg\`，核 `FFMPEG_SHA256` 后解到 `tools\ffmpeg\`
> 并随载荷分发；缓存命中就不再联网。换版本时改那三个常量。

> **提示**：全量打包要求工作区干净——包里的提交号必须真的代表这份代码，有未提交改动时 `build.py` 会报错退出。确实要打一个带改动的包就加 `--allow-dirty`，`build-info.json` 里会标 `dirty` 为真。
>
> 打包前还会按 mtime 判断 `flandora/webui/dist/index.html` 是否比前端源码旧：旧了才调用 npm 重建。想直接复用现有产物就加 `--skip-web`，它会打印两边的时间；产物缺失时该参数会直接报错，不会打出没有界面的包。

取钥器单独打包，因为 frida 有几十 MB，不塞进主程序。代码结构、依赖规则与扩展点见 [architecture.md](architecture.md)，工程约定与验收口径见 [AGENTS.md](../AGENTS.md)。

## 验收

自动化测试看的是「同样的输入产生同样的输出」；UIA、真机微信与真实的 OneBot 客户端连接只能用清单验。

发布前先跑验收脚本，它把能自动核的部分做掉：

```powershell
python tools/smoke.py           # 交付物身份、端口、自启项、打包自检
python tools/smoke.py --live    # 额外发一次停止信号，验证端口释放与进程退出（会停掉正在跑的桥接）
```

脚本只读时不动任何东西；`--live` 会停桥接，跑之前确认没有正在用的实例。剩下的真机项目按 [smoke-test.md](smoke-test.md) 走一遍，每一步都给了可观察的判据，最后把实际看到的结果记在当节下面。

九条自动闸门（语法检查、ruff、pytest、测试收集闸、配置样例、版本单一来源、打包校验、前端测试与构建、真机冒烟）的分工与判定口径写在 [AGENTS.md](../AGENTS.md)。

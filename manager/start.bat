@echo off
REM 双击运行：启动 Bridge 管理器（无黑色 cmd 窗口）
setlocal
set "HERE=%~dp0"
REM 优先用 py 启动器，其次用 python；自动探测，无需硬编码路径
where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3 "%HERE%bridge_manager.py"
) else (
    start "" python "%HERE%bridge_manager.py"
)
endlocal

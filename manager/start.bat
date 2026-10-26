@echo off
chcp 65001 >nul
REM 双击运行：启动 Flandora 管理器（常驻托盘，无黑色 cmd 窗口）
setlocal
set "HERE=%~dp0"
REM 优先用仓库虚拟环境（管理器要 pystray / Pillow 两个包），没有才退回 pyw / pythonw
if exist "%HERE%..\.venv\Scripts\pythonw.exe" (
    start "" "%HERE%..\.venv\Scripts\pythonw.exe" "%HERE%main.py"
) else if exist "%HERE%..\.venv\Scripts\python.exe" (
    start "" "%HERE%..\.venv\Scripts\python.exe" "%HERE%main.py"
) else (
    where pyw >nul 2>nul
    if %errorlevel%==0 (
        start "" pyw -3 "%HERE%main.py"
    ) else (
        start "" pythonw "%HERE%main.py"
    )
)
endlocal

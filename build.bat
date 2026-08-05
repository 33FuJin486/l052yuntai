@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_VENV=.venv"
set "APP_DIST=dist\云台视觉跟踪系统"

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 Windows Python Launcher。请安装 Python 3.10.x 后重试。
    pause
    exit /b 1
)

if not exist "%APP_VENV%\Scripts\python.exe" (
    echo [1/5] 创建 Python 3.10 虚拟环境...
    py -3.10 -m venv "%APP_VENV%"
    if errorlevel 1 goto :failed
)

call "%APP_VENV%\Scripts\activate.bat"

echo [2/5] 安装/更新构建依赖...
python -m pip install --upgrade pip wheel setuptools
if errorlevel 1 goto :failed
rem 第一版固定从 PyTorch 官方 CPU 仓库安装，避免无意打入 CUDA 运行库。
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto :failed
python -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo [3/5] 清理旧构建并运行 PyInstaller...
python -m PyInstaller "云台视觉跟踪系统.spec" --clean --noconfirm
if errorlevel 1 goto :failed

echo [4/5] 复制外部模型、配置和使用说明...
if not exist "%APP_DIST%\model" mkdir "%APP_DIST%\model"
if not exist "%APP_DIST%\config" mkdir "%APP_DIST%\config"
copy /Y "model\best.pt" "%APP_DIST%\model\best.pt" >nul
copy /Y "config\settings.json" "%APP_DIST%\config\settings.json" >nul
copy /Y "README.md" "%APP_DIST%\README.md" >nul
if errorlevel 1 goto :failed

echo [5/5] 构建完成。
echo 发布目录：%CD%\%APP_DIST%
echo 请复制整个文件夹到无 Python 的 Windows 测试机进行验收。
pause
exit /b 0

:failed
echo.
echo [ERROR] 构建失败，请查看上方错误信息。
pause
exit /b 1

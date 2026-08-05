"""开发环境与 PyInstaller onedir 环境的统一路径。"""

from __future__ import annotations

import sys
from pathlib import Path


def application_root() -> Path:
    """返回外部资源根目录。

    源码运行时为项目根目录；PyInstaller 运行时为 EXE 所在目录。
    模型和配置始终保留为 EXE 同级的外部文件。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative_path: str | Path) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return (application_root() / path).resolve()


def portable_path(path: str | Path) -> str:
    """尽可能把绝对路径保存成相对应用目录的可移植路径。"""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(application_root()).as_posix()
    except ValueError:
        return str(resolved)

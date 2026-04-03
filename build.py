"""
PyInstaller 打包脚本

用法:
    python build.py
"""

import subprocess
import sys
import os
import urllib.request
import json

REPO_OWNER = "ZGMFX01A"
REPO_NAME = "mouse-battery"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"


def _parse_version(version_str: str) -> tuple[int, int, int]:
    """将版本字符串转为可比较元组。支持 v1.5.3 / 1.5.3。"""
    v = (version_str or "").strip().lower().lstrip('v')
    try:
        parts = v.split('.')
        if len(parts) != 3:
            return (0, 0, 0)
        return tuple(int(p) for p in parts)
    except Exception:
        return (0, 0, 0)


def _read_local_version(version_file: str) -> str:
    try:
        with open(version_file, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return "v0.0.0"


def _fetch_latest_github_version(timeout: int = 5) -> str:
    """获取 GitHub 最新 release tag，失败返回空字符串。"""
    try:
        req = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={'User-Agent': 'MouseBattery-Build'}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            tag = data.get('tag_name', '').strip()
            return tag
    except Exception:
        return ""


def sync_version_file() -> str:
    """
    本地打包前自动同步 VERSION：
    - 若 GitHub 最新 release tag 更高，则自动写入 VERSION
    - 若网络不可用或无更新，保持本地 VERSION 不变
    返回最终使用的版本号
    """
    base_dir = os.path.dirname(__file__) or '.'
    version_file = os.path.join(base_dir, 'VERSION')

    local_version = _read_local_version(version_file)
    remote_version = _fetch_latest_github_version()

    if remote_version and _parse_version(remote_version) > _parse_version(local_version):
        with open(version_file, 'w', encoding='utf-8') as f:
            f.write(remote_version)
        print(f"[VERSION] Synced VERSION from {local_version} -> {remote_version}")
        return remote_version

    if remote_version:
        print(f"[VERSION] Keep local VERSION: {local_version} (latest release: {remote_version})")
    else:
        print(f"[VERSION] Keep local VERSION: {local_version} (GitHub unavailable)")

    return local_version

def build():
    """使用 PyInstaller 打包为单文件 exe"""

    final_version = sync_version_file()
    print(f"[VERSION] Build version: {final_version}")

    # 检查 PyInstaller
    try:
        import PyInstaller
        print(f"PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("Error: PyInstaller not installed. Run: pip install pyinstaller")
        sys.exit(1)

    # 获取 flet 和 flet_desktop 的路径
    import flet
    flet_dir = os.path.dirname(flet.__file__)
    
    # assets 目录路径
    assets_dir = os.path.join(os.path.dirname(__file__) or '.', 'assets')
    # app.ico 文件路径（用于运行时 Windows API 设置窗口图标）
    ico_file = os.path.join(os.path.dirname(__file__) or '.', 'app.ico')
    
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--noconsole',
        '--name', 'MouseBattery',
        '--clean',
        # Flet 依赖资源
        '--add-data', f'{flet_dir};flet',
        # 应用 assets（含窗口图标等）
        '--add-data', f'{assets_dir};assets',
        # app.ico 放到根目录供 ctypes LoadImageW 加载
        '--add-data', f'{ico_file};.',
        # VERSION 文件放到根目录供 config.py 读取版本号
        '--add-data', f'{os.path.join(os.path.dirname(__file__) or ".", "VERSION")};.',
    ]

    try:
        import flet_desktop
        flet_desktop_dir = os.path.dirname(flet_desktop.__file__)
        cmd.extend(['--add-data', f'{flet_desktop_dir};flet_desktop'])
    except ImportError:
        print("Warning: flet_desktop not found, skipping. (GUI may not run later)")

    # 隐式导入和其他启动文件
    cmd.extend([
        '--hidden-import', 'pystray._win32',
        '--hidden-import', 'PIL',
        '--hidden-import', 'hid',
        '--hidden-import', 'flet',
        '--hidden-import', 'gui',
        '--hidden-import', 'updater',
        'main.py',
    ])

    # 如果有图标文件
    ico_path = os.path.join(os.path.dirname(__file__), 'app.ico')
    if os.path.exists(ico_path):
        cmd.extend(['--icon', ico_path])

    print(f"Run: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=os.path.dirname(__file__) or '.')
    if result.returncode == 0:
        print("\n[SUCCESS] Build complete! Output: dist/MouseBattery.exe")
    else:
        print(f"\n[ERROR] Build failed, return code: {result.returncode}")
    return result.returncode

if __name__ == '__main__':
    sys.exit(build())

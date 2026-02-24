"""
PyInstaller 打包脚本

用法:
    python build.py
"""

import subprocess
import sys
import os

def build():
    """使用 PyInstaller 打包为单文件 exe"""

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

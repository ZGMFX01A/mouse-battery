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
import importlib

REPO_OWNER = "ZGMFX01A"
REPO_NAME = "mouse-battery"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"

# 私有核心依赖的发行名：
# - 用于错误提示里告诉维护者缺少哪个 pip 包
# - 与 `.private-requirements.txt` 中约定的安装名保持一致
PRIVATE_CORE_DISTRIBUTION_NAME = "mouse-battery-core"
# 私有核心依赖的导入根包名：
# - 运行时桥接层和 PyInstaller 收集逻辑都依赖这个稳定入口
# - 一旦改名，需同步修改 design / workflow / 本地构建说明
PRIVATE_CORE_IMPORT_NAME = "mouse_battery_core"
# 受版本控制的私有 core 引用文件：
# - 公开壳通过它固定 CI 应拉取哪个私有仓库 ref
# - 本地开发也可从中读取推荐的 editable 仓库路径
PRIVATE_CORE_REFERENCE_FILE = os.path.join(os.path.dirname(__file__) or '.', 'private-core-ref.json')
# 兼容旧的本地演练目录：
# - 之前私有核心临时放在公开仓库忽略目录 `.private-core-src/`
# - 这次切到独立私有仓库后仍保留旧目录探测，避免已有环境立即失效
PRIVATE_CORE_LEGACY_SOURCE_ROOT = os.path.join(os.path.dirname(__file__) or '.', '.private-core-src')


def load_private_core_reference() -> dict:
    """读取受版本控制的私有 core 引用配置。"""
    reference = {
        'distribution_name': PRIVATE_CORE_DISTRIBUTION_NAME,
        'import_name': PRIVATE_CORE_IMPORT_NAME,
        'ref': 'main',
        'ci_checkout_path': 'mouse-battery-core',
        'local_editable_path': '../mouse-battery-core',
    }

    if not os.path.exists(PRIVATE_CORE_REFERENCE_FILE):
        return reference

    try:
        with open(PRIVATE_CORE_REFERENCE_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            reference.update({key: value for key, value in payload.items() if value})
    except Exception as exc:
        # 这里要让维护者在构建日志中明确看到版本锚点未按预期生效，
        # 不能静默吞掉引用文件损坏问题。
        print(f"[WARN] Failed to read {os.path.basename(PRIVATE_CORE_REFERENCE_FILE)}: {exc}")

    return reference


def discover_private_core_source_roots(private_core_module, reference: dict) -> list[str]:
    """收集 PyInstaller 需要额外感知的私有源码根目录。"""
    package_name = reference.get('import_name') or PRIVATE_CORE_IMPORT_NAME
    roots: list[str] = []
    seen_roots: set[str] = set()

    module_file = getattr(private_core_module, '__file__', None)
    if module_file:
        # 已安装模块的 `__file__` 最接近真实源码落点，
        # 对 editable install 与本地 checkout 都比硬编码目录更稳。
        roots.append(os.path.dirname(os.path.dirname(os.path.abspath(module_file))))

    for candidate in (
        reference.get('ci_checkout_path'),
        reference.get('local_editable_path'),
        PRIVATE_CORE_LEGACY_SOURCE_ROOT,
    ):
        if not candidate:
            continue

        candidate_path = candidate
        if not os.path.isabs(candidate_path):
            candidate_path = os.path.abspath(os.path.join(os.path.dirname(__file__) or '.', candidate_path))

        if not os.path.isdir(candidate_path):
            continue

        if not os.path.isdir(os.path.join(candidate_path, package_name)):
            continue

        roots.append(candidate_path)

    deduplicated_roots: list[str] = []
    for root in roots:
        normalized_root = os.path.normcase(os.path.abspath(root))
        if normalized_root in seen_roots:
            continue
        seen_roots.add(normalized_root)
        deduplicated_roots.append(root)

    return deduplicated_roots


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
        # utf-8-sig：CI 里 PowerShell Out-File 写入的 VERSION 带 BOM，
        # 必须吞掉，否则 ﻿ 混进版本号导致解析和 cp1252 打印双双出错。
        with open(version_file, 'r', encoding='utf-8-sig') as f:
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


def ensure_private_core_available():
    """确认私有核心依赖已安装，并在缺失时显式失败。

    这里故意不去检查 `.private-requirements.txt` 是否存在，原因是：
    - 本地开发者可能已经提前把私有包安装进当前环境
    - CI 也可能在前置步骤里先完成安装再进入 `build.py`

    构建脚本真正关心的是“当前解释器能不能导入私有核心”，
    这样才能把失败点稳定收敛到 PyInstaller 启动前，而不是生成一个
    运行即崩的 exe。
    """
    try:
        return importlib.import_module(PRIVATE_CORE_IMPORT_NAME)
    except ImportError as exc:
        print(
            "[ERROR] Missing private core dependency. "
            f"Install `{PRIVATE_CORE_DISTRIBUTION_NAME}` before running build.py. "
            "Expected input file: .private-requirements.txt"
        )
        raise SystemExit(1) from exc


def extend_pyinstaller_for_private_core(cmd: list[str], private_core_module, reference: dict):
    """把私有核心显式加入 PyInstaller 收集范围。

    私有核心后续可能演进出子模块动态导入或额外 helper 模块；
    这里先通过 hidden-import + collect-submodules 双保险，避免出现
    “环境里能 import，但 onefile 产物缺模块”的经典问题。
    """
    if not private_core_module:
        return

    import_name = reference.get('import_name') or PRIVATE_CORE_IMPORT_NAME

    for source_root in discover_private_core_source_roots(private_core_module, reference):
        # editable install 下，私有核心源码既可能来自 sibling 私有仓库，
        # 也可能仍来自旧的 `.private-core-src/` 演练目录；
        # 显式加入这些根目录，才能让 PyInstaller 稳定解析真实子模块文件。
        cmd.extend(['--paths', os.path.abspath(source_root)])

    cmd.extend([
        '--hidden-import', import_name,
        '--hidden-import', f'{import_name}.logitech_hid',
        '--hidden-import', f'{import_name}.razer_hid',
        '--hidden-import', f'{import_name}.keyboard_hid',
        '--collect-submodules', import_name,
    ])

def build():
    """使用 PyInstaller 打包为单文件 exe"""

    final_version = sync_version_file()
    print(f"[VERSION] Build version: {final_version}")
    private_core_reference = load_private_core_reference()

    # 私有核心是本次构建链路的强约束：
    # 缺失时必须在 PyInstaller 启动前失败，不能静默降级或继续生成无效产物。
    private_core_module = ensure_private_core_available()

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
        # --clean：打包前清理 PyInstaller 缓存，避免旧构建残留混入新 exe，
        # 否则旧缓存可能导致 onefile 解压后模块不全（python312.dll 加载失败的诱因之一）。
        '--clean',
        # --noconfirm：无交互环境下直接覆盖 dist，避免打包脚本卡在交互提示。
        '--noconfirm',
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
        # asyncio 在 Windows 上的 IOCP 事件循环依赖 _overlapped C 扩展，
        # flet 导入 asyncio.windows_events 时需要；PyInstaller 静态分析
        # 偶尔漏收，onefile 运行时会报 "No module named '_overlapped'"。
        '--hidden-import', '_overlapped',
        '--hidden-import', 'asyncio.windows_events',
        '--hidden-import', 'asyncio.windows_utils',
        '--hidden-import', 'pystray._win32',
        '--hidden-import', 'PIL',
        '--hidden-import', 'hid',
        '--hidden-import', 'flet',
        '--hidden-import', 'gui',
        # flet 0.80+ 内部使用动态导入加载多个子模块，PyInstaller 静态分析
        # 容易漏掉，导致 onefile 运行时缺模块；这里强制收集全 flet 子模块，
        # 保证 _MEIPASS 解压后入口能完整加载依赖，避免 python312.dll 之后
        # 又出现 flet.core 之类 ModuleNotFoundError。
        '--collect-submodules', 'flet',
        '--collect-submodules', 'flet_desktop',
        '--hidden-import', 'updater',
        'main.py',
    ])

    extend_pyinstaller_for_private_core(cmd, private_core_module, private_core_reference)

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

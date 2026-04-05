"""
Github Release 自动更新模块

通过拉取最新 Release，检查标签版本并提供下载热替换的方法。
"""
import os
import sys
import json
import logging
import subprocess
import tempfile
import urllib.request
import urllib.error
from urllib.error import URLError, HTTPError
from typing import Optional

logger = logging.getLogger(__name__)

REPO_OWNER = "ZGMFX01A"
REPO_NAME = "mouse-battery"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"

def parse_version(version_str: str) -> tuple:
    """提取版本号数字 (v1.3.0 -> (1, 3, 0))"""
    v = version_str.lower().strip().lstrip('v')
    try:
        return tuple(map(int, v.split('.')))
    except ValueError:
        return (0, 0, 0)


def _normalize_version_text(version_str: str) -> str:
    """统一版本文本格式（去除前缀 v/V、空白）。"""
    return version_str.lower().strip().lstrip('v')


def _pick_release_asset(assets: list, latest_version: str) -> dict:
    """
    从 release assets 中挑选最匹配当前 tag 的 exe。

    规则：
    1) 仅考虑 .exe
    2) 优先文件名包含最新版本号（如 1.5.5）
    3) 再按 updated_at 降序兜底
    """
    exe_assets = [a for a in assets if a.get('name', '').lower().endswith('.exe')]
    if not exe_assets:
        return {}

    ver = _normalize_version_text(latest_version)
    matched = [a for a in exe_assets if ver and ver in a.get('name', '').lower()]
    candidates = matched if matched else exe_assets

    candidates.sort(key=lambda a: a.get('updated_at', ''), reverse=True)
    return candidates[0] if candidates else {}


def check_for_update(current_version: str) -> tuple[bool, str, str, str]:
    """
    检查更新
    返回 (是否有更新, 最新版号, 下载链接, 更新日志)
    """
    try:
        req = urllib.request.Request(API_URL, headers={'User-Agent': 'MouseBattery-Updater'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            latest_version = data.get('tag_name', '')
            body = data.get('body', '')
            if not body:
                body = "（此次发布未提供更新日志说明）"
            
            assets = data.get('assets', [])

            selected = _pick_release_asset(assets, latest_version)
            download_url = selected.get('browser_download_url', '')
            selected_name = selected.get('name', '')
                    
            if not download_url:
                logger.error("Release 中未发现 .exe 产物")
                return False, current_version, "", ""

            logger.info(
                f"更新检查命中资源: tag={latest_version}, asset={selected_name or '<unknown>'}"
            )
                
            current_tup = parse_version(current_version)
            latest_tup = parse_version(latest_version)
            
            if latest_tup > current_tup:
                return True, latest_version, download_url, body
            
            return False, latest_version, "", ""
            
    except Exception as e:
        logger.error(f"检查更新失败: {e}")
        return False, "", "", str(e)


def download_and_install(download_url: str, on_progress=None, host_pid: Optional[int] = None):
    """
    下载并准备替换当前文件。然后自动重启应用程序。
    如果当前是脚本运行，则直接中断（不覆盖脚本本身）。
    """
    # 如果没被 PyInstaller 打包过，则不执行覆盖重启操作
    if not getattr(sys, 'frozen', False):
        logger.info("当前处于代码调试模式，跳过覆盖更新。如果打包，会自动替换文件。")
        return False

    exe_path = sys.executable
    old_exe_path = exe_path + ".old"
    new_exe_path = exe_path + ".new"
    swap_script_path = os.path.join(
        tempfile.gettempdir(),
        f"mouse_battery_swap_{os.getpid()}.cmd"
    )
    target_pid = host_pid if isinstance(host_pid, int) and host_pid > 0 else os.getpid()
    
    try:
        # 1. 下载新文件到 .new（避免边运行边改名当前 exe 导致卡死）
        req = urllib.request.Request(download_url, headers={'User-Agent': 'MouseBattery-Updater'})
        with urllib.request.urlopen(req, timeout=30) as response:
            content_len = response.info().get('Content-Length', '0').strip()
            total_size = int(content_len) if content_len.isdigit() else 0
            downloaded = 0
            chunk_size = 1024 * 16 # 16KB 缓冲
            
            with open(new_exe_path, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total_size > 0:
                        pct = int(downloaded / total_size * 100)
                        on_progress(pct, downloaded, total_size)
        
        logger.info(f"新版本下载完成: {new_exe_path}")

        # 2. 通过外部脚本完成替换与拉起，避免当前进程内自改名引发冻结
        script_lines = [
            "@echo off",
            "setlocal enabledelayedexpansion",
            "set RETRY=0",
            ":retry",
            "if %RETRY% GEQ 80 goto fail",  # 最多约 20 秒
            f"taskkill /F /T /PID {target_pid} >nul 2>nul",
            f"if exist \"{old_exe_path}\" del /f /q \"{old_exe_path}\" >nul 2>nul",
            f"if exist \"{exe_path}\" move /y \"{exe_path}\" \"{old_exe_path}\" >nul 2>nul",
            f"move /y \"{new_exe_path}\" \"{exe_path}\" >nul 2>nul",
            f"if exist \"{exe_path}\" goto run",
            "set /a RETRY=%RETRY%+1",
            "ping 127.0.0.1 -n 2 >nul",
            "goto retry",
            ":run",
            f"start \"\" \"{exe_path}\"",
            f"del /f /q \"{swap_script_path}\" >nul 2>nul",
            "exit /b 0",
            ":fail",
            f"del /f /q \"{new_exe_path}\" >nul 2>nul",
            f"del /f /q \"{swap_script_path}\" >nul 2>nul",
            "exit /b 1",
        ]
        with open(swap_script_path, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write("\r\n".join(script_lines) + "\r\n")

        subprocess.Popen(
            ['cmd', '/c', swap_script_path],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        # 3. 立即退出旧进程，让外部脚本接管替换
        os._exit(0)
                        
    except Exception as e:
        logger.error(f"应用更新失败: {e}")
        # 失败清理：尽量移除半成品 .new
        if os.path.exists(new_exe_path):
            try:
                os.remove(new_exe_path)
            except Exception:
                pass
        return False

def clean_old_version():
    """程序启动时调用，专门用于抹除上次更新留下的 .old 僵尸外壳"""
    if getattr(sys, 'frozen', False):
        old_exe_path = sys.executable + ".old"
        if os.path.exists(old_exe_path):
            try:
                os.remove(old_exe_path)
                logger.info(f"发现并清理旧版本执行文件: {old_exe_path}")
            except Exception as e:
                logger.error(f"清理遗留文件遇到错误: {e}")

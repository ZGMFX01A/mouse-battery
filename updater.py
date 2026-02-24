"""
Github Release 自动更新模块

通过拉取最新 Release，检查标签版本并提供下载热替换的方法。
"""
import os
import sys
import json
import logging
import subprocess
import urllib.request
import urllib.error
from urllib.error import URLError, HTTPError
import threading

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
            assets = data.get('assets', [])
            
            # 找到 exe 下载链接
            download_url = ""
            for asset in assets:
                if asset.get('name', '').endswith('.exe'):
                    download_url = asset.get('browser_download_url', '')
                    break
                    
            if not download_url:
                logger.error("Release 中未发现 .exe 产物")
                return False, current_version, "", ""
                
            current_tup = parse_version(current_version)
            latest_tup = parse_version(latest_version)
            
            if latest_tup > current_tup:
                return True, latest_version, download_url, body
            
            return False, latest_version, "", ""
            
    except Exception as e:
        logger.error(f"检查更新失败: {e}")
        return False, "", "", str(e)


def download_and_install(download_url: str, on_progress=None):
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
    
    try:
        # 1. 之前可能有没删干净的 .old，这里试着清理兜个底
        if os.path.exists(old_exe_path):
            try:
                os.remove(old_exe_path)
            except Exception:
                pass
                
        # 2. 将正在运行的本体改名 (Windows 允许重命名正在执行的文件)
        os.rename(exe_path, old_exe_path)
        
        # 3. 下载新文件到原路径
        req = urllib.request.Request(download_url, headers={'User-Agent': 'MouseBattery-Updater'})
        with urllib.request.urlopen(req, timeout=30) as response:
            total_size = int(response.info().get('Content-Length').strip())
            downloaded = 0
            chunk_size = 1024 * 16 # 16KB 缓冲
            
            with open(exe_path, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total_size > 0:
                        pct = int(downloaded / total_size * 100)
                        on_progress(pct, downloaded, total_size)
        
        logger.info(f"新版本已成功下载并就位: {exe_path}")
        
        # 4. 热重启自身
        subprocess.Popen([exe_path], creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        
        # 5. 立刻送死，结束旧进程生命周期
        # 这里的硬退会导致 atexit 等不完全触发，因为我们要极速让位
        os._exit(0)
                        
    except Exception as e:
        logger.error(f"应用更新失败: {e}")
        # 如果改名后下载失败了，得抓紧改回来抢救一下
        if not os.path.exists(exe_path) and os.path.exists(old_exe_path):
            try:
                os.rename(old_exe_path, exe_path)
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

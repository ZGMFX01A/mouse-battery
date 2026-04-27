"""
Github Release 自动更新模块

通过拉取最新 Release，检查标签版本并提供下载热替换的方法。
"""
import os
import sys
import json
import logging
import _thread
import subprocess
import tempfile
import urllib.request
import urllib.error
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse, unquote
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


def _target_exe_name_from_url(download_url: str, fallback_name: str) -> str:
    """从下载链接推导目标 exe 文件名。"""
    try:
        path = urlparse(download_url).path
        candidate = os.path.basename(unquote(path)).strip()
        if candidate.lower().endswith('.exe'):
            return candidate
    except Exception:
        pass
    return fallback_name


def _shutdown_request_path(pid: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"mouse_battery_shutdown_{pid}.json")


def request_process_shutdown(pid: int, reason: str = "update", skip_gui_pid: Optional[int] = None):
    """向目标进程发送优雅退出请求。"""
    if not isinstance(pid, int) or pid <= 0:
        return

    payload = {"reason": reason}
    if isinstance(skip_gui_pid, int) and skip_gui_pid > 0:
        payload["skip_gui_pid"] = skip_gui_pid

    try:
        with open(_shutdown_request_path(pid), 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        logger.info(f"已写入进程退出请求: pid={pid}, reason={reason}, skip_gui_pid={skip_gui_pid}")
    except Exception as e:
        logger.error(f"写入进程退出请求失败: pid={pid}, err={e}")


def consume_shutdown_request(pid: int) -> dict:
    """读取并消费指定进程的退出请求。"""
    request_path = _shutdown_request_path(pid)
    if not os.path.exists(request_path):
        return {}

    try:
        with open(request_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取进程退出请求失败: pid={pid}, err={e}")
        return {}
    finally:
        try:
            os.remove(request_path)
        except Exception:
            pass


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

    current_pid = os.getpid()
    exe_path = sys.executable
    exe_dir = os.path.dirname(exe_path)
    current_exe_name = os.path.basename(exe_path)
    target_exe_name = _target_exe_name_from_url(download_url, current_exe_name)
    target_exe_path = os.path.join(exe_dir, target_exe_name)
    old_exe_path = exe_path + ".old"
    download_exe_path = target_exe_path + ".download"
    swap_script_path = os.path.join(
        tempfile.gettempdir(),
        f"mouse_battery_swap_{current_pid}.cmd"
    )
    managed_host_pid = host_pid if isinstance(host_pid, int) and host_pid > 0 and host_pid != current_pid else None
    
    try:
        # 1. 下载新文件到目标旁边的 .download 临时文件
        req = urllib.request.Request(download_url, headers={'User-Agent': 'MouseBattery-Updater'})
        with urllib.request.urlopen(req, timeout=30) as response:
            content_len = response.info().get('Content-Length', '0').strip()
            total_size = int(content_len) if content_len.isdigit() else 0
            downloaded = 0
            chunk_size = 1024 * 16 # 16KB 缓冲
            
            with open(download_exe_path, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total_size > 0:
                        pct = int(downloaded / total_size * 100)
                        on_progress(pct, downloaded, total_size)
        
        logger.info(
            f"新版本下载完成: temp={download_exe_path}, target={target_exe_path}, current={exe_path}"
        )

        # 2. 通过外部脚本等待进程优雅退出后，再完成替换与拉起
        script_lines = [
            "@echo off",
            "setlocal enabledelayedexpansion",
            f"set CURRENT_PID={current_pid}",
            f"set HOST_PID={managed_host_pid or 0}",
            "set RETRY=0",
            ":wait_exit",
            "set STILL_RUNNING=",
            "tasklist /FI \"PID eq %CURRENT_PID%\" | find \"%CURRENT_PID%\" >nul && set STILL_RUNNING=1",
            "if not \"%HOST_PID%\"==\"0\" tasklist /FI \"PID eq %HOST_PID%\" | find \"%HOST_PID%\" >nul && set STILL_RUNNING=1",
            "if not defined STILL_RUNNING goto swap",
            "if %RETRY% GEQ 80 goto force_kill",
            "set /a RETRY=%RETRY%+1",
            "ping 127.0.0.1 -n 2 >nul",
            "goto wait_exit",
            ":force_kill",
            "taskkill /F /T /PID %CURRENT_PID% >nul 2>nul",
            "if not \"%HOST_PID%\"==\"0\" taskkill /F /T /PID %HOST_PID% >nul 2>nul",
            "set RETRY=0",
            ":wait_after_kill",
            "set STILL_RUNNING=",
            "tasklist /FI \"PID eq %CURRENT_PID%\" | find \"%CURRENT_PID%\" >nul && set STILL_RUNNING=1",
            "if not \"%HOST_PID%\"==\"0\" tasklist /FI \"PID eq %HOST_PID%\" | find \"%HOST_PID%\" >nul && set STILL_RUNNING=1",
            "if not defined STILL_RUNNING goto swap",
            "if %RETRY% GEQ 40 goto fail",
            "set /a RETRY=%RETRY%+1",
            "ping 127.0.0.1 -n 2 >nul",
            "goto wait_after_kill",
            ":swap",
        ]

        if os.path.normcase(target_exe_path) == os.path.normcase(exe_path):
            script_lines.extend([
                f"if exist \"{old_exe_path}\" del /f /q \"{old_exe_path}\" >nul 2>nul",
                f"if exist \"{exe_path}\" move /y \"{exe_path}\" \"{old_exe_path}\" >nul 2>nul",
                f"move /y \"{download_exe_path}\" \"{target_exe_path}\" >nul 2>nul",
            ])
        else:
            script_lines.extend([
                f"if exist \"{target_exe_path}\" del /f /q \"{target_exe_path}\" >nul 2>nul",
                f"move /y \"{download_exe_path}\" \"{target_exe_path}\" >nul 2>nul",
                f"if exist \"{old_exe_path}\" del /f /q \"{old_exe_path}\" >nul 2>nul",
                f"if exist \"{exe_path}\" move /y \"{exe_path}\" \"{old_exe_path}\" >nul 2>nul",
            ])

        script_lines.extend([
            f"if exist \"{target_exe_path}\" goto run",
            ":run",
            f"start \"\" \"{target_exe_path}\"",
            "ping 127.0.0.1 -n 3 >nul",
            f"if exist \"{old_exe_path}\" del /f /q \"{old_exe_path}\" >nul 2>nul",
        ])

        if os.path.normcase(target_exe_path) != os.path.normcase(exe_path):
            script_lines.append(f"if exist \"{exe_path}\" del /f /q \"{exe_path}\" >nul 2>nul")

        script_lines.extend([
            f"del /f /q \"{swap_script_path}\" >nul 2>nul",
            "exit /b 0",
            ":fail",
            f"del /f /q \"{download_exe_path}\" >nul 2>nul",
            f"del /f /q \"{swap_script_path}\" >nul 2>nul",
            "exit /b 1",
        ])
        with open(swap_script_path, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write("\r\n".join(script_lines) + "\r\n")

        subprocess.Popen(
            ['cmd', '/c', swap_script_path],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        # 3. 请求宿主进程优雅退出，并中断当前主线程触发自身清理
        if managed_host_pid:
            request_process_shutdown(managed_host_pid, reason="update", skip_gui_pid=current_pid)
        logger.info("热更新脚本已启动，准备优雅退出当前进程")
        _thread.interrupt_main()
        return True
                        
    except Exception as e:
        logger.error(f"应用更新失败: {e}")
        # 失败清理：尽量移除半成品 .download
        if os.path.exists(download_exe_path):
            try:
                os.remove(download_exe_path)
            except Exception:
                pass
        return False

def clean_old_version():
    """程序启动时调用，专门用于抹除上次更新留下的 .old 僵尸外壳"""
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        for name in os.listdir(exe_dir):
            if not name.lower().endswith('.exe.old'):
                continue
            old_exe_path = os.path.join(exe_dir, name)
            try:
                os.remove(old_exe_path)
                logger.info(f"发现并清理旧版本执行文件: {old_exe_path}")
            except Exception as e:
                logger.error(f"清理遗留文件遇到错误: {e}")

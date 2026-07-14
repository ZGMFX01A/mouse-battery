"""
Github Release 自动更新模块

通过拉取最新 Release，检查标签版本并提供下载热替换的方法。
"""
import os
import re
import sys
import json
import hashlib
import logging
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import urllib.error
from urllib.error import URLError, HTTPError
from typing import Optional

logger = logging.getLogger(__name__)

REPO_OWNER = "ZGMFX01A"
REPO_NAME = "mouse-battery"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
SHUTDOWN_REQUEST_PREFIX = "mouse_battery_shutdown"
DOWNLOAD_MIRROR_PREFIX = "https://ghfast.top/"
MIN_VALID_EXE_BYTES = 1024 * 1024

# --- IPv4 优先解析 ---------------------------------------------------------
# Windows 上 urllib 没有浏览器的 Happy Eyeballs：若系统拿到 IPv6 地址但路由
# 不通，socket 会先在 IPv6 上耗尽整个连接超时才回退 IPv4，表现为"浏览器
# 下载几秒、程序里几分钟没反应"。这里把 getaddrinfo 结果按 IPv4 优先排序，
# 只在本模块的网络调用期间生效（加锁串行，避免污染其他线程的并发解析）。
_ipv4_lock = threading.Lock()
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_first_getaddrinfo(*args, **kwargs):
    results = _orig_getaddrinfo(*args, **kwargs)
    return sorted(results, key=lambda ai: 0 if ai[0] == socket.AF_INET else 1)


def _urlopen(url: str, timeout: float, retries: int = 1, on_retry=None):
    """带 IPv4 优先与一次重试的 urlopen。"""
    req = urllib.request.Request(url, headers={'User-Agent': 'MouseBattery-Updater'})
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(retries + 1):
        try:
            with _ipv4_lock:
                socket.getaddrinfo = _ipv4_first_getaddrinfo
                try:
                    return urllib.request.urlopen(req, timeout=timeout)
                finally:
                    socket.getaddrinfo = _orig_getaddrinfo
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.warning(f"请求失败将重试({attempt + 1}/{retries}): {url}, err={e}")
                if on_retry:
                    on_retry(attempt + 1, retries, e)
                time.sleep(1)
    raise last_err


def parse_version(version_str: str) -> tuple:
    """提取版本号数字。

    用正则截取开头的 X.Y.Z，兼容 v1.3.0 / 1.3.0 以及带后缀描述的
    tag（如 "v2.0.1-修复xxx"），解析失败返回 (0, 0, 0)。
    """
    m = re.match(r'v?(\d+)\.(\d+)\.(\d+)', (version_str or "").strip().lower())
    if not m:
        return (0, 0, 0)
    return tuple(int(p) for p in m.groups())


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


def check_for_update(current_version: str) -> tuple[bool, str, str, str, int, str]:
    """
    检查更新
    返回 (是否有更新, 最新版号, 下载链接, 更新日志, 文件大小, SHA-256)
    """
    try:
        with _urlopen(API_URL, timeout=8, retries=1) as response:
            data = json.loads(response.read().decode('utf-8'))

            latest_version = data.get('tag_name', '')
            body = data.get('body', '')
            if not body:
                body = "（此次发布未提供更新日志说明）"

            assets = data.get('assets', [])

            selected = _pick_release_asset(assets, latest_version)
            download_url = selected.get('browser_download_url', '')
            selected_name = selected.get('name', '')
            asset_size = int(selected.get('size', 0) or 0)
            asset_digest = str(selected.get('digest', '') or '')

            if not download_url:
                logger.error("Release 中未发现 .exe 产物")
                return False, current_version, "", "", 0, ""

            logger.info(
                f"更新检查命中资源: tag={latest_version}, asset={selected_name or '<unknown>'}"
            )

            current_tup = parse_version(current_version)
            latest_tup = parse_version(latest_version)

            if latest_tup > current_tup:
                return True, latest_version, download_url, body, asset_size, asset_digest

            return False, latest_version, "", "", 0, ""

    except Exception as e:
        logger.error(f"检查更新失败: {e}")
        return False, "", "", str(e), 0, ""


def _normalize_sha256(digest: str) -> str:
    """从 GitHub asset digest 中提取可用于强校验的 SHA-256。"""
    match = re.fullmatch(r'sha256:([0-9a-fA-F]{64})', (digest or '').strip())
    return match.group(1).lower() if match else ''


def _notify_status(on_status, stage: str, detail: str = '') -> None:
    if not on_status:
        return
    try:
        on_status(stage, detail)
    except Exception as e:
        logger.warning(f"更新状态回调失败: stage={stage}, err={e}")


def _download_to_path(url: str, target_path: str, on_progress=None,
                      expected_size: int = 0, retries: int = 0,
                      on_retry=None) -> tuple[int, str]:
    """下载单个来源并返回实际字节数与 SHA-256。"""
    for attempt in range(retries + 1):
        try:
            with _urlopen(url, timeout=20, retries=0) as response:
                content_len = response.info().get('Content-Length', '0').strip()
                response_size = int(content_len) if content_len.isdigit() else 0
                total_size = expected_size if expected_size > 0 else response_size
                downloaded = 0
                hasher = hashlib.sha256()
                read_chunk = getattr(response, 'read1', response.read)

                with open(target_path, 'wb') as f:
                    while True:
                        chunk = read_chunk(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            pct = int(downloaded / total_size * 100) if total_size > 0 else -1
                            on_progress(min(pct, 100), downloaded, total_size)

            return downloaded, hasher.hexdigest()
        except Exception as e:
            if attempt >= retries:
                raise
            if on_retry:
                on_retry(attempt + 1, retries, e)
            time.sleep(1)

    raise RuntimeError("更新下载重试耗尽")


def _validate_download(target_path: str, downloaded: int, actual_sha256: str,
                       expected_size: int, expected_sha256: str) -> int:
    """校验单个下载来源；只有通过校验才算该来源成功。"""
    if downloaded <= 0:
        raise RuntimeError("下载到的更新文件为空")
    if downloaded < MIN_VALID_EXE_BYTES:
        raise RuntimeError(f"下载到的更新文件过小 ({downloaded} 字节)，疑似截断下载")

    actual_size = os.path.getsize(target_path)
    if actual_size != downloaded:
        raise RuntimeError(
            f"已下载文件大小不一致: expected={downloaded}, actual={actual_size}"
        )
    if expected_size > 0 and actual_size != expected_size:
        raise RuntimeError(
            f"更新文件大小校验失败: expected={expected_size}, actual={actual_size}"
        )
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"更新文件 SHA-256 校验失败: expected={expected_sha256}, actual={actual_sha256}"
        )
    return actual_size


def _safe_remove(path: str) -> None:
    """安全删除文件，并把清理失败写入日志，避免更新残留被静默吞掉。"""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.warning(f"删除临时文件失败: {path}, err={e}")


def _get_shutdown_request_path(target_pid: int) -> str:
    """返回目标进程的热更新退出请求文件路径。"""
    return os.path.join(tempfile.gettempdir(), f"{SHUTDOWN_REQUEST_PREFIX}_{target_pid}.json")


def request_process_shutdown(target_pid: int, reason: str = "update",
                             skip_gui_pid: Optional[int] = None) -> bool:
    """写入热更新退出请求，让目标进程先做优雅收尾再退出。"""
    if not isinstance(target_pid, int) or target_pid <= 0:
        logger.error(f"写入退出请求失败，目标 PID 非法: {target_pid!r}")
        return False

    request_path = _get_shutdown_request_path(target_pid)
    temp_path = request_path + ".tmp"
    payload = {
        "reason": reason,
        "target_pid": target_pid,
        "requester_pid": os.getpid(),
        "requested_at": time.time(),
    }
    if isinstance(skip_gui_pid, int) and skip_gui_pid > 0:
        # GUI 触发热更新时，需要让主进程退出收尾时跳过当前 GUI 子进程，
        # 否则下载线程会在替换前被主进程的 atexit 清理误杀。
        payload["skip_gui_pid"] = skip_gui_pid

    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(temp_path, request_path)
        logger.info(
            f"已写入退出请求: target_pid={target_pid}, requester_pid={payload['requester_pid']}, "
            f"skip_gui_pid={payload.get('skip_gui_pid')}"
        )
        return True
    except Exception as e:
        logger.error(f"写入退出请求失败: {e}")
        _safe_remove(temp_path)
        return False


def consume_shutdown_request(current_pid: int) -> Optional[dict]:
    """读取并消费当前进程的热更新退出请求。"""
    if not isinstance(current_pid, int) or current_pid <= 0:
        logger.warning(f"读取退出请求时收到非法 PID: {current_pid!r}")
        return None

    request_path = _get_shutdown_request_path(current_pid)
    if not os.path.exists(request_path):
        return None

    try:
        with open(request_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        logger.error(f"读取退出请求失败: path={request_path}, err={e}")
        _safe_remove(request_path)
        return None

    try:
        os.remove(request_path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"删除已消费的退出请求失败: path={request_path}, err={e}")

    if not isinstance(payload, dict):
        logger.error(f"退出请求格式非法，已忽略: {payload!r}")
        return None

    return payload


def _build_swap_script_lines(exe_path: str, old_exe_path: str,
                             new_exe_path: str, swap_script_path: str,
                             target_pid: int,
                             expected_size: int) -> list[str]:
    """构造外部替换脚本。

    先等待目标进程自行退出；若超时仍未退出，再回退到强制结束。
    这样可以优先走 [`start_update_shutdown_watchdog()`](main.py:93) 的优雅收尾，
    同时保留最终兜底能力，避免更新永久卡死。

    expected_size 用于在替换前校验新 exe 字节数，拦截截断下载导致
    PyInstaller onefile 解压后找不到 python312.dll 的情况。
    """
    return [
        "@echo off",
        "setlocal enabledelayedexpansion",
        "set WAIT_RETRY=0",
        ":wait_exit",
        "if %WAIT_RETRY% GEQ 15 goto force_kill",
        f'tasklist /FI "PID eq {target_pid}" 2>nul | find /I "{target_pid}" >nul',
        "if errorlevel 1 goto swap",
        "set /a WAIT_RETRY=%WAIT_RETRY%+1",
        "ping 127.0.0.1 -n 2 >nul",
        "goto wait_exit",
        ":force_kill",
        "set KILL_RETRY=0",
        ":kill_retry",
        "if %KILL_RETRY% GEQ 20 goto fail",
        f'taskkill /F /T /PID {target_pid} >nul 2>nul',
        f'tasklist /FI "PID eq {target_pid}" 2>nul | find /I "{target_pid}" >nul',
        "if errorlevel 1 goto swap",
        "set /a KILL_RETRY=%KILL_RETRY%+1",
        "ping 127.0.0.1 -n 2 >nul",
        "goto kill_retry",
        ":swap",
        # 替换前先校验新 exe 大小是否与下载字节数一致。
        # PyInstaller onefile 被"截断下载"后，bootloader 解压会找不到
        # python312.dll（内嵌资源不全），报 MEIxxxxx\python312.dll 找不到。
        # 这里通过字节数门槛拦截不完整产物，不达标的直接回滚保留旧 exe。
        f'if not exist "{new_exe_path}" goto verify_fail',
        # 用 for 取文件字节数；脚本文件里 %%~zI 是双百分号转义，运行时即 %~zI。
        # 取到后用 !NEW_SIZE!（延迟展开）读取，因为 NEW_SIZE 在同一批处理段内
        # 刚刚被 set，用 %NEW_SIZE% 普通展开会拿到旧值导致校验失效。
        f'for %%I in ("{new_exe_path}") do set NEW_SIZE=%%~zI',
        f'if not defined NEW_SIZE goto verify_fail',
        f'if !NEW_SIZE! NEQ {expected_size} goto verify_fail',
        "set SWAP_RETRY=0",
        ":swap_retry",
        "if %SWAP_RETRY% GEQ 20 goto fail",
        f'if exist "{old_exe_path}" del /f /q "{old_exe_path}" >nul 2>nul',
        f'if exist "{exe_path}" move /y "{exe_path}" "{old_exe_path}" >nul 2>nul',
        f'move /y "{new_exe_path}" "{exe_path}" >nul 2>nul',
        f'if exist "{exe_path}" goto run',
        "set /a SWAP_RETRY=%SWAP_RETRY%+1",
        "ping 127.0.0.1 -n 2 >nul",
        "goto swap_retry",
        ":run",
        f'start "" "{exe_path}"',
        f'del /f /q "{swap_script_path}" >nul 2>nul',
        "exit /b 0",
        # 校验失败：不替换，清掉坏文件并退出，保留旧 exe 可继续运行。
        ":verify_fail",
        f'del /f /q "{new_exe_path}" >nul 2>nul',
        f'del /f /q "{swap_script_path}" >nul 2>nul',
        "exit /b 2",
        ":fail",
        f'del /f /q "{new_exe_path}" >nul 2>nul',
        f'del /f /q "{swap_script_path}" >nul 2>nul',
        "exit /b 1",
    ]


def download_and_install(download_url: str, on_progress=None, host_pid: Optional[int] = None,
                         expected_size: int = 0, expected_digest: str = '', on_status=None):
    """
    下载并准备替换当前文件。然后自动重启应用程序。
    如果当前是脚本运行，则直接中断（不覆盖脚本本身）。
    """
    # 如果没被 PyInstaller 打包过，则不执行覆盖重启操作
    if not getattr(sys, 'frozen', False):
        logger.info("当前处于代码调试模式，跳过覆盖更新。如果打包，会自动替换文件。")
        _notify_status(on_status, 'error', 'debug_mode')
        return False

    current_pid = os.getpid()
    exe_path = sys.executable
    old_exe_path = exe_path + ".old"
    new_exe_path = exe_path + ".new"
    swap_script_path = os.path.join(
        tempfile.gettempdir(),
        f"mouse_battery_swap_{current_pid}.cmd"
    )
    # 优先由外部传入宿主主进程 PID（GUI 热更新场景），否则用自身 PID
    target_pid = host_pid if isinstance(host_pid, int) and host_pid > 0 else current_pid
    skip_gui_pid = current_pid if target_pid != current_pid else None

    try:
        # 1. 官方直链优先；只有 GitHub 提供了 SHA-256 时才允许经第三方镜像下载。
        expected_sha256 = _normalize_sha256(expected_digest)
        if not expected_sha256:
            raise RuntimeError("Release 未提供有效的 SHA-256，已拒绝自动更新")
        sources = [('official', download_url)]
        if download_url.startswith('https://github.com/'):
            sources.append(('mirror', DOWNLOAD_MIRROR_PREFIX + download_url))

        downloaded = 0
        actual_size = 0
        actual_sha256 = ''
        last_download_error: Optional[Exception] = None
        for index, (source_name, source_url) in enumerate(sources):
            _safe_remove(new_exe_path)
            _notify_status(on_status, 'connecting', source_name)

            def on_retry(attempt, retries, error):
                _notify_status(on_status, 'retrying', f'{source_name}:{attempt}/{retries}:{error}')

            try:
                downloaded, actual_sha256 = _download_to_path(
                    source_url,
                    new_exe_path,
                    on_progress=on_progress,
                    expected_size=expected_size,
                    retries=1 if source_name == 'mirror' else 0,
                    on_retry=on_retry,
                )
                _notify_status(on_status, 'verifying')
                actual_size = _validate_download(
                    new_exe_path,
                    downloaded,
                    actual_sha256,
                    expected_size,
                    expected_sha256,
                )
                last_download_error = None
                break
            except Exception as e:
                last_download_error = e
                logger.warning(f"更新下载来源失败: source={source_name}, err={type(e).__name__}: {e}")
                if index + 1 < len(sources):
                    _notify_status(on_status, 'fallback', str(e))

        if last_download_error is not None:
            raise last_download_error

        logger.info(
            f"新版本下载并校验完成: {new_exe_path}, size={actual_size} bytes, "
            f"sha256={actual_sha256}"
        )

        # 2. 通过外部脚本完成替换与拉起，避免当前进程内自改名引发冻结
        #    路径全部加引号，避免含空格/中文路径出错；编码使用本地 OEM 兼容
        script_lines = _build_swap_script_lines(
            exe_path=exe_path,
            old_exe_path=old_exe_path,
            new_exe_path=new_exe_path,
            swap_script_path=swap_script_path,
            target_pid=target_pid,
            expected_size=actual_size,
        )
        with open(swap_script_path, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write("\r\n".join(script_lines) + "\r\n")

        subprocess.Popen(
            ['cmd', '/c', swap_script_path],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        shutdown_requested = request_process_shutdown(
            target_pid=target_pid,
            reason="update",
            skip_gui_pid=skip_gui_pid,
        )
        if shutdown_requested:
            logger.info(
                f"已通知目标进程退出，等待外部脚本执行替换: "
                f"target_pid={target_pid}, current_pid={current_pid}"
            )
        else:
            logger.warning("退出请求写入失败，将依赖外部脚本超时后强制结束目标进程")

        # GUI 作为子进程发起更新时，应立即退出自身释放文件占用；
        # 宿主主进程则由 watchdog 接管优雅退出，避免下载线程被过早中断。
        if target_pid != current_pid:
            import atexit
            logger.info(f"GUI 更新进程准备退出，交由主进程完成热更新收尾: host_pid={target_pid}")
            atexit._run_exitfuncs()
            os._exit(0)

        return True

    except Exception as e:
        logger.error(f"应用更新失败: {type(e).__name__}: {e}")
        _notify_status(on_status, 'error', str(e))
        _safe_remove(new_exe_path)
        _safe_remove(swap_script_path)
        return False

def clean_old_version():
    """程序启动时清理上次更新留下的旧版本或未完成下载。"""
    if getattr(sys, 'frozen', False):
        for suffix in ('.old', '.new'):
            stale_path = sys.executable + suffix
            if not os.path.exists(stale_path):
                continue
            try:
                os.remove(stale_path)
                logger.info(f"发现并清理更新遗留文件: {stale_path}")
            except Exception as e:
                logger.error(f"清理更新遗留文件失败: path={stale_path}, err={e}")

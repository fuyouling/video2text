"""系统代理检测工具。

优先读取 config.ini 的 [app] proxy（用户显式配置），
当其为空时自动探测本机已配置的代理（Windows 系统代理 / IE LAN 设置、
PAC 自动配置脚本、环境变量 HTTP_PROXY / HTTPS_PROXY 等），帮助用户免去手动填写代理。

设计原则：
- config.ini 的显式代理永远优先（用户意图最高）。
- 仅在未显式配置时才探测系统代理，避免把失效/不需要的系统代理强加给用户。
- 探测失败（如无代理、注册表读取异常）时安全返回空字符串（直连）。
"""

import os
from typing import Optional

try:
    import winreg  # type: ignore
except ImportError:
    winreg = None


def detect_system_proxy() -> str:
    """探测本机已配置的代理地址。

    探测顺序：
    1. Windows 系统代理（注册表 ``Software\\Microsoft\\Windows\\CurrentVersion\\
       Internet Settings`` 的 ``ProxyEnable`` / ``ProxyServer`` / ``AutoConfigURL``）。
       - 支持 ``ip:port``、``http=ip:port;https=ip:port;socks=ip:port`` 形式，
         自动提取可用协议地址并规范化。
       - 支持 PAC 脚本 (AutoConfigURL)。
    2. 环境变量 ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY``（不区分大小写）。
    3. 均未配置则返回空字符串（表示直连）。

    Returns:
        形如 ``http://127.0.0.1:7890`` 或 ``socks5://127.0.0.1:1080`` 的代理地址；未检测到返回 ``""``。
    """
    proxy = _detect_windows_proxy()
    if proxy:
        return proxy

    proxy = _detect_env_proxy()
    if proxy:
        return proxy

    return ""


def _normalize_proxy(value: str, default_scheme: str = "http://") -> str:
    """规范化代理地址：补全协议头，去除空白，规范化 socks 协议。"""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        if not default_scheme.endswith("://"):
            default_scheme = f"{default_scheme}://"
        value = default_scheme + value
    elif value.startswith("socks://"):
        value = "socks5://" + value[len("socks://"):]
    return value


def _detect_windows_proxy() -> str:
    """从 Windows 注册表读取系统代理设置或 PAC 脚本。使用上下文管理器确保句柄关闭。"""
    if winreg is None:
        return ""

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            try:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except OSError:
                enabled = 0

            proxy_server = ""
            if enabled:
                try:
                    proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
                except OSError:
                    proxy_server = ""

            # 1. 优先使用显式启用的 ProxyServer
            if enabled and proxy_server:
                # ProxyServer 可能为 "ip:port" 或 "http=ip:port;https=ip:port;socks=ip:port"
                if "=" in proxy_server:
                    parts = {}
                    for item in proxy_server.split(";"):
                        if "=" in item:
                            scheme, addr = item.split("=", 1)
                            parts[scheme.strip().lower()] = addr.strip()
                    for scheme in ("https", "http", "socks"):
                        if scheme in parts:
                            default_scheme = "socks5://" if scheme == "socks" else f"{scheme}://"
                            return _normalize_proxy(parts[scheme], default_scheme=default_scheme)
                    # 取任意一个可用协议
                    for scheme, addr in parts.items():
                        if addr:
                            default_scheme = "socks5://" if scheme == "socks" else f"{scheme}://"
                            return _normalize_proxy(addr, default_scheme=default_scheme)
                    return ""
                return _normalize_proxy(proxy_server)

            # 2. 支持检测 AutoConfigURL (PAC 脚本)
            try:
                autoconfig_url, _ = winreg.QueryValueEx(key, "AutoConfigURL")
            except OSError:
                autoconfig_url = ""

            if autoconfig_url and autoconfig_url.strip():
                return _normalize_proxy(autoconfig_url.strip())

            return ""
    except Exception:
        return ""


def _detect_env_proxy() -> str:
    """从环境变量读取代理（HTTP_PROXY / HTTPS_PROXY / ALL_PROXY）。"""
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name, "")
        if value:
            default_scheme = "socks5://" if "socks" in name.lower() or "all" in name.lower() else "http://"
            return _normalize_proxy(value, default_scheme=default_scheme)
    return ""


def resolve_proxy(settings_proxy: str) -> str:
    """解析最终使用的代理地址。

    - 若 config.ini 显式配置了代理，直接返回（用户意图优先）。
    - 否则探测系统代理；探测到则返回，未探测到返回空字符串（直连）。

    Args:
        settings_proxy: 来自 Settings().get("app.proxy", "") 的值。

    Returns:
        最终代理地址字符串（可能为空，表示直连）。
    """
    if settings_proxy and settings_proxy.strip():
        return settings_proxy.strip()
    return detect_system_proxy()


def get_proxy_for_display(settings_proxy: str) -> str:
    """返回用于日志/界面展示的代理来源说明。

    仅用于提示用户当前代理来自「config.ini」还是「系统自动探测」。
    """
    if settings_proxy and settings_proxy.strip():
        return settings_proxy.strip()
    detected = detect_system_proxy()
    return detected

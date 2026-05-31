"""统一的 API 限流与 429 处理"""

import random
import threading
import time
from typing import Optional


class RateLimiter:
    """速率限制器，确保两次操作间隔不低于 min_interval 秒。"""

    def __init__(self, min_interval: float = 1.5):
        self._lock = threading.Lock()
        self._min_interval = min_interval
        self._last_time = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_time = time.monotonic()


def get_retry_after(response_headers: dict) -> Optional[float]:
    """解析 Retry-After 头，返回秒数（float）。"""
    raw = response_headers.get("Retry-After", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    return None


def is_rate_limit(response) -> bool:
    """判断 HTTP 响应是否为 429 限流。"""
    return response.status_code == 429


def exponential_backoff(attempt: int, base: float = 2.0) -> float:
    """指数退避: base^attempt + jitter"""
    return base**attempt + random.uniform(0, 0.5)

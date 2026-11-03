"""统一的 API 限流与 429 处理"""

import datetime
from email.utils import parsedate_to_datetime
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
        """获取速率许可。锁内不执行 sleep，避免长时间阻塞并发线程。"""
        wait_time = 0.0
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed < self._min_interval:
                wait_time = self._min_interval - elapsed
                self._last_time = now + wait_time
            else:
                self._last_time = now

        if wait_time > 0:
            time.sleep(wait_time)


def get_retry_after(response_headers: dict) -> Optional[float]:
    """解析 Retry-After 头，支持秒数（float）或 RFC 7231 HTTP-Date 格式，返回等待秒数。"""
    raw = ""
    for k, v in response_headers.items():
        if str(k).lower() == "retry-after":
            raw = str(v).strip()
            break
    if not raw:
        return None

    # 1. 尝试直接解析秒数
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass

    # 2. 尝试解析 RFC 7231 HTTP-Date 格式 (例如: "Wed, 21 Oct 2026 07:28:00 GMT")
    try:
        dt = parsedate_to_datetime(raw)
        now = datetime.datetime.now(datetime.timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        diff = (dt - now).total_seconds()
        return max(0.0, float(diff))
    except Exception:
        pass

    return None


def is_rate_limit(response) -> bool:
    """判断 HTTP 响应是否为 429 限流。"""
    return getattr(response, "status_code", None) == 429


def exponential_backoff(attempt: int, base: float = 2.0, max_backoff: float = 60.0) -> float:
    """指数退避: min(base^attempt, max_backoff) + jitter，且最大不超过 max_backoff。"""
    backoff = min(base**attempt, max_backoff) + random.uniform(0, 0.5)
    return min(backoff, max_backoff)

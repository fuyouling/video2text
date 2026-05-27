"""subprocess 兼容常量 —— 跨平台 CREATE_NO_WINDOW"""

import subprocess
import sys

CREATE_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

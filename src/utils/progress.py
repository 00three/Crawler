"""콘솔에서 여러 소스의 진행률을 보여주기 위한 간단한 ProgressBar.

stdout 라인을 갱신하는 단일 라인 진행률 표시. tqdm 같은 외부 의존성이 없어
서버/로그 환경에서도 안전하다.
"""

import sys
import time


class ProgressBar:
    def __init__(self, width=30):
        self.width = width
        self._last_line_len = 0
        self._started_at = time.time()

    def _format_bar(self, current, total):
        total = max(total, 1)
        ratio = min(max(current / total, 0.0), 1.0)
        filled = int(self.width * ratio)
        return "[" + "#" * filled + "-" * (self.width - filled) + f"] {int(ratio * 100):3d}%"

    def _print(self, line):
        # 이전 라인을 공백으로 덮어쓰고 캐리지 리턴
        pad = max(self._last_line_len - len(line), 0)
        sys.stdout.write("\r" + line + " " * pad)
        sys.stdout.flush()
        self._last_line_len = len(line)

    def update(self, source, current, total, status=""):
        bar = self._format_bar(current, total)
        suffix = f" {status}" if status else ""
        line = f"{source:<12} {bar} {current}/{total}{suffix}"
        self._print(line)

    def finish(self, source, current, total, status="done"):
        bar = self._format_bar(current, total)
        line = f"{source:<12} {bar} {current}/{total} {status}"
        self._print(line)
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._last_line_len = 0

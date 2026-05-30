"""스킬 쿨다운 내부 타이머.

OCR 최초 anchor + monotonic 기반 실시간 감산. 매 프레임 OCR 없어도 정확.
OCR 과 ±3초 이내면 drift 로그, 초과면 re-anchor.

사용처: `HealerWorker` — 파력무참 / 백호의희원 / 백호의희원첨.
"""
from __future__ import annotations

import time


class SkillCdTimer:
    """파력/백호 등 스킬 쿨다운 내부 타이머.

    OCR은 최초 anchor + 드리프트 보정용. 매 프레임 OCR 없어도 monotonic으로
    실시간 계산 가능. OCR과 ±3초 이내면 drift 로그만, 초과면 re-anchor.
    """

    def __init__(self, name: str, log):
        self.name = name
        self.log = log
        self._anchor_ts: float = 0.0     # monotonic, anchor 시점.
        self._anchor_sec: int = -1       # OCR이 읽은 남은 초 (>=0).
        self._last_log_ts: float = 0.0   # drift 로그 스로틀.

    def on_ocr(self, sec: int) -> None:
        if sec is None or sec < 0:
            return
        now = time.monotonic()
        if self._anchor_sec < 0 or sec > self.remaining() + 3:
            # 새 쿨 걸림 or 드리프트 초과: 재앵커.
            old = self.remaining()
            self._anchor_ts = now
            self._anchor_sec = int(sec)
            try:
                if (now - self._last_log_ts) >= 1.0:
                    self._last_log_ts = now
                    self.log.info(
                        f"[TIMER-{self.name}] anchor={sec}s (prev remaining={old})"
                    )
            except Exception:
                pass
            return
        # drift 비교 (OCR 값이 내부 계산과 얼마나 다른지).
        rem = self.remaining()
        diff = int(sec) - rem
        if abs(diff) >= 3:
            # 3초 이상 벌어지면 OCR 우선 (쿨 재적용 가능).
            self._anchor_ts = now
            self._anchor_sec = int(sec)
            try:
                self.log.info(
                    f"[TIMER-{self.name}] re-anchor ocr={sec} internal={rem} diff={diff:+d}"
                )
            except Exception:
                pass
        else:
            try:
                if (now - self._last_log_ts) >= 5.0:
                    self._last_log_ts = now
                    self.log.info(
                        f"[TIMER-{self.name}] drift ocr={sec} internal={rem} diff={diff:+d}"
                    )
            except Exception:
                pass

    def remaining(self) -> int:
        if self._anchor_sec < 0:
            return -1
        elapsed = time.monotonic() - self._anchor_ts
        rem = int(round(self._anchor_sec - elapsed))
        if rem <= 0:
            self._anchor_sec = -1
            self._anchor_ts = 0.0
            return 0
        return rem

    def clear(self) -> None:
        self._anchor_sec = -1
        self._anchor_ts = 0.0

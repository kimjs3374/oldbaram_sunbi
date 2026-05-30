"""Input dispatcher — KeyAdapter abstraction for OS input layer.

Wraps src/input/keys.py via injected adapter.

Design ref: §2.6 + §11.2 (src/input/keys.py port)
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Callable, Dict, Optional, Protocol, Set, Tuple

log = logging.getLogger("src_v2.hands.input")


class KeyAdapter(Protocol):
    """Low-level key/mouse adapter."""
    def key_down(self, vk: int) -> None: ...
    def key_up(self, vk: int) -> None: ...
    def key_tap(self, vk: int, hold_ms: int = 30) -> None: ...
    def mouse_click(self, x: int, y: int, button: str = "left") -> None: ...


class NullKeys:
    """No-op adapter — for tests / dry-run mode. Records calls for inspection."""

    def __init__(self):
        self.events: list = []
        self._down: Set[int] = set()
        self._lock = threading.Lock()

    def key_down(self, vk: int) -> None:
        with self._lock:
            self.events.append(("down", vk))
            self._down.add(vk)

    def key_up(self, vk: int) -> None:
        with self._lock:
            self.events.append(("up", vk))
            self._down.discard(vk)

    def key_tap(self, vk: int, hold_ms: int = 30) -> None:
        with self._lock:
            self.events.append(("tap", vk, hold_ms))

    def mouse_click(self, x: int, y: int, button: str = "left") -> None:
        with self._lock:
            self.events.append(("click", x, y, button))

    def is_down(self, vk: int) -> bool:
        with self._lock:
            return vk in self._down

    def reset(self):
        with self._lock:
            self.events.clear()
            self._down.clear()


# Direction to VK map (Windows VK_LEFT=0x25, VK_UP=0x26, VK_RIGHT=0x27, VK_DOWN=0x28)
DIRECTION_VK: Dict[str, int] = {
    "U": 0x26,
    "D": 0x28,
    "L": 0x25,
    "R": 0x27,
}


class InputDispatcher:
    """Direction key holder. Ensures only one of U/D/L/R held at a time.

    Used by Muscle main loop. Thread-safe.
    """

    def __init__(self, keys: Optional[KeyAdapter] = None,
                 movement_lock_stuck_sec: float = 10.0):
        """v1 1:1 movement_lock 안전장치 포함.

        v1 SoR (healer_worker.py:1516-1539):
          - blocks_movement=True 스킬 시전 중 방향키 press 차단.
          - lock True→False edge 시 외부 콜백 (재hold 예약).
          - 10초 stuck 감지 시 강제 해제 + 재hold edge.
        """
        self.keys: KeyAdapter = keys or NullKeys()
        self._held: Optional[str] = None
        self._lock = threading.Lock()
        # movement_lock 상태 (v1 keys.set_movement_lock).
        self._movement_locked: bool = False
        self._movement_lock_since: float = 0.0
        self._movement_lock_stuck_sec: float = float(movement_lock_stuck_sec)
        self._was_movement_locked: bool = False
        # 재hold 예약 콜백 (lock False→True 또는 stuck 강제해제 시 호출).
        self._on_lock_release: Optional[Callable[[], None]] = None

    def set_movement_lock(self, locked: bool) -> None:
        """v1 keys.set_movement_lock 1:1.

        True → 방향키 press 즉시 release_all + 이후 set_direction 무시.
        False → 다음 set_direction 호출 정상 동작 + on_lock_release 콜백.
        """
        with self._lock:
            prev = self._movement_locked
            self._movement_locked = bool(locked)
            now = time.time()
            if locked and not prev:
                # lock 진입 — 현재 held 방향 release.
                self._movement_lock_since = now
                if self._held is not None:
                    vk = DIRECTION_VK.get(self._held)
                    if vk is not None:
                        try:
                            self.keys.key_up(vk)
                        except Exception:  # noqa: BLE001
                            log.exception("lock_release fail dir=%s", self._held)
                    self._held = None
            elif not locked and prev:
                # lock 해제 — edge 콜백 (재hold 예약은 외부 muscle_loop 가 처리).
                self._movement_lock_since = 0.0
                cb = self._on_lock_release
        # 콜백은 lock 밖에서 호출 (deadlock 방지).
        if not locked and prev and self._on_lock_release is not None:
            try:
                self._on_lock_release()
            except Exception:  # noqa: BLE001
                log.exception("on_lock_release callback fail")

    def is_movement_locked(self) -> bool:
        with self._lock:
            return self._movement_locked

    def set_on_lock_release(self, cb: Optional[Callable[[], None]]) -> None:
        """lock True→False edge 콜백 등록 (재hold 예약 hook)."""
        self._on_lock_release = cb

    def check_movement_lock_stuck(self) -> bool:
        """v1 1:1: lock 10초 초과 시 강제 해제 + 재hold 예약 콜백.

        Muscle loop 가 매 iter 호출. 진짜 해제됐으면 True 반환.
        """
        with self._lock:
            if not self._movement_locked or self._movement_lock_since == 0.0:
                return False
            elapsed = time.time() - self._movement_lock_since
            if elapsed <= self._movement_lock_stuck_sec:
                return False
            # 강제 해제.
            self._movement_locked = False
            self._movement_lock_since = 0.0
        # 콜백은 lock 밖.
        log.warning("[LOCK-STUCK] movement_lock %ds 초과 → 강제 해제",
                    int(self._movement_lock_stuck_sec))
        if self._on_lock_release is not None:
            try:
                self._on_lock_release()
            except Exception:  # noqa: BLE001
                log.exception("on_lock_release callback fail")
        return True

    def set_direction(self, want: str) -> None:
        """Set held direction. '-' or '' or None means release all.

        v1 1:1: movement_locked=True 면 무시 (자힐 SEQ-A 중 방향키 차단).
        """
        if want is None or want == "" or want == "-":
            want = None
        with self._lock:
            if self._movement_locked:
                # 잠금 중 — held 그대로 유지하지만 새 press 안 함.
                return
            if want == self._held:
                return
            # release current
            if self._held is not None:
                vk = DIRECTION_VK.get(self._held)
                if vk is not None:
                    try:
                        self.keys.key_up(vk)
                    except Exception:  # noqa: BLE001
                        log.exception("key_up failed dir=%s", self._held)
            # press new
            if want is not None:
                vk = DIRECTION_VK.get(want)
                if vk is None:
                    log.warning("unknown direction: %r", want)
                    self._held = None
                    return
                try:
                    self.keys.key_down(vk)
                except Exception:  # noqa: BLE001
                    log.exception("key_down failed dir=%s", want)
            self._held = want

    def held_direction(self) -> Optional[str]:
        with self._lock:
            return self._held

    def release_all(self) -> None:
        self.set_direction(None)

    def tap(self, vk: int, hold_ms: int = 30) -> None:
        self.keys.key_tap(vk, hold_ms)

    def tap_numpad_direct(self, vk: int) -> bool:
        """NumPad VK 의 scan code 를 그대로 송신 (nvk 변환 없음).

        v1 SoR: src/input/numlock_cycle.py:158 press_numpad_direct.
        파혼술 등 cycler 토글 대상이 아닌 직접 시전 NumPad 스킬용.
        매핑 없는 VK 는 일반 tap fallback.
        """
        try:
            from src.input.numlock_cycle import press_numpad_direct  # type: ignore
            return bool(press_numpad_direct(int(vk)))
        except Exception:
            log.exception("tap_numpad_direct fallback to tap vk=%s", hex(int(vk)))
            try:
                self.keys.key_tap(int(vk), 50)
            except Exception:
                pass
            return False

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.keys.mouse_click(x, y, button)

"""Win32 전역 단축키 (RegisterHotKey) 래퍼.

블록 A/B 실행 버튼처럼 msw.exe 포그라운드에서도 동작해야 하는 키 바인딩용.
QShortcut 은 메인 창 포커스 없을 때 동작 안 함 → Win32 호출 사용.

사용:
    mgr = GlobalHotkeys(log_fn=print)
    mgr.register("block_a", VK_F11, callback=fn_a)
    mgr.register("block_b", VK_F12, callback=fn_b)
    mgr.start()
    ...
    mgr.stop()

callback 은 hotkey 스레드에서 호출됨. 장시간 작업이면 signal/queue 로 메인
스레드에 넘겨야 함.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Callable, Dict, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

VK_F11 = 0x7A
VK_F12 = 0x7B


class GlobalHotkeys:
    def __init__(self, log_fn: Optional[Callable[[str], None]] = None):
        self._log = log_fn or (lambda _s: None)
        self._lock = threading.Lock()
        # id → (mods, vk, cb, name, alternates)
        self._pending: Dict[int, tuple] = {}
        self._callbacks: Dict[int, Callable[[], None]] = {}
        self._next_id: int = 1
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._stop_evt = threading.Event()
        self._started = False

    def register(self, name: str, vk: int,
                 callback: Callable[[], None],
                 mods: int = 0,
                 alternates: Optional[list] = None) -> int:
        """핫키 등록 예약. start() 후 스레드에서 실제 RegisterHotKey 수행.

        alternates: 주 (mods, vk) 등록 실패 시 순서대로 재시도할 대안
        [(mods, vk), ...]. 예: F12 가 선점되면 (MOD_CONTROL, VK_F12) 시도.

        반환: 할당된 hotkey id (내부 식별용).
        """
        alts = list(alternates) if alternates else []
        with self._lock:
            hid = self._next_id
            self._next_id += 1
            self._pending[hid] = (
                int(mods) | MOD_NOREPEAT, int(vk), callback, str(name), alts,
            )
            self._callbacks[hid] = callback
        self._log(f"[HOTKEY] pending register name={name} id={hid} "
                  f"vk=0x{vk:02X} mods=0x{mods:04X} alts={len(alts)}")
        return hid

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="global_hotkeys", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        # 메시지 루프 깨우기.
        if self._thread_id:
            try:
                user32.PostThreadMessageW(
                    wt.DWORD(self._thread_id), wt.UINT(0x0012),  # WM_QUIT
                    wt.WPARAM(0), wt.LPARAM(0),
                )
            except Exception:
                pass
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        self._started = False

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        # 메시지 큐 초기화 (RegisterHotKey 전 1회 PeekMessage 필요).
        msg = wt.MSG()
        user32.PeekMessageW(
            ctypes.byref(msg), None, 0, 0, 0
        )
        # 등록. 실패 시 alternates 순회.
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
        for hid, (mods, vk, _cb, name, alts) in pending.items():
            attempts = [(mods, vk)] + [
                (int(m) | MOD_NOREPEAT, int(v)) for (m, v) in alts
            ]
            registered = False
            for (m, v) in attempts:
                ok = user32.RegisterHotKey(None, hid, m, v)
                if ok:
                    self._log(
                        f"[HOTKEY] RegisterHotKey OK name={name} id={hid} "
                        f"vk=0x{v:02X} mods=0x{m:04X}"
                    )
                    registered = True
                    break
                err = ctypes.get_last_error()
                self._log(
                    f"[HOTKEY] RegisterHotKey 실패 name={name} id={hid} "
                    f"vk=0x{v:02X} mods=0x{m:04X} err={err} "
                    f"{'(이미 선점됨-alt 시도)' if err == 1409 and (m, v) != attempts[-1] else ''}"
                )
            if not registered:
                self._log(
                    f"[HOTKEY] ⚠ name={name} 모든 조합 실패 — 단축키 비활성. "
                    f"테스트 버튼 사용 필요."
                )
        # 메시지 루프.
        GetMessageW = user32.GetMessageW
        GetMessageW.restype = ctypes.c_int
        while not self._stop_evt.is_set():
            ret = GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == WM_HOTKEY:
                hid = int(msg.wParam)
                cb = self._callbacks.get(hid)
                if cb is not None:
                    try:
                        cb()
                    except Exception as e:
                        self._log(f"[HOTKEY] 콜백 예외 id={hid}: {e}")
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        # 해제.
        for hid in list(self._callbacks.keys()):
            try:
                user32.UnregisterHotKey(None, hid)
            except Exception:
                pass
        self._thread_id = 0

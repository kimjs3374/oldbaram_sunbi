"""UI Publisher — runs in its own thread, builds payload from Snapshot, emits at fixed Hz.

Muscle main loop never calls emit. UiPublisher does it all.

Design ref: §2.9
"""
from __future__ import annotations
import logging
import threading
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, Optional

from ..core.snapshot import SnapshotStore

log = logging.getLogger("src_v2.ui.publisher")


# Type for emit callable: takes a single dict payload
EmitCallable = Callable[[Dict[str, Any]], None]


class _StateStub:
    """v1 main_window 호환 stub — .value, .name 호출 + str/repr 모두 한국어.

    이전에 _default_payload 안의 local class 였음 → repr 노출 + 매 프레임 새
    클래스 생성 비용. 모듈 레벨로 빼고 __repr__ = __str__ 강제.
    """
    __slots__ = ("value", "name")

    def __init__(self, v: str) -> None:
        self.value = str(v)
        self.name = str(v)

    def __str__(self) -> str:
        return self.value

    __repr__ = __str__

    def __format__(self, spec: str) -> str:
        return format(self.value, spec)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _StateStub):
            return self.value == other.value
        return self.value == other

    def __hash__(self) -> int:
        return hash(self.value)


class UiPublisher(threading.Thread):
    """Publishes UI payloads at hz Hz from SnapshotStore.

    `emit` is a callable (e.g. PyQt signal.emit, or test stub).
    Failure in emit doesn't crash thread.
    """

    def __init__(self,
                 store: SnapshotStore,
                 emit: EmitCallable,
                 hz: int = 15,
                 build_payload: Optional[Callable[[Any], Dict[str, Any]]] = None,
                 watchers: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(daemon=True, name="ui_publisher")
        self.store = store
        self.emit = emit
        self.interval = 1.0 / max(1, hz)
        self.build_payload = build_payload or self._default_payload
        self._stop_evt = threading.Event()
        self._emit_count = 0
        self._err_count = 0
        # perf_tuple — capture/yolo/ocr/total 의 _last_dur_ms 합산용.
        self._watchers = watchers or {}

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout=timeout)

    def run(self) -> None:
        log.info("ui publisher start hz=%.1f", 1.0 / self.interval)
        while not self._stop_evt.wait(self.interval):
            try:
                snap = self.store.read()
                payload = self.build_payload(snap)
                self.emit(payload)
                self._emit_count += 1
            except Exception as e:  # noqa: BLE001
                self._err_count += 1
                log.exception("ui publisher emit err: %s", e)
        log.info("ui publisher stop emit=%d", self._emit_count)

    def _perf_tuple(self) -> tuple:
        try:
            g = float(getattr(self._watchers.get("capture"), "_last_dur_ms", 0.0))
            y = float(getattr(self._watchers.get("yolo"), "_last_dur_ms", 0.0))
            o = float(getattr(self._watchers.get("ocr"), "_last_dur_ms", 0.0))
            return (g, y, o, g + y + o)
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)

    def _default_payload(self, snap) -> Dict[str, Any]:
        # 2026-04-25 v1 main_window._on_frame 호환 키 전수 노출.
        # main_window 가 frame_ready signal payload 에서 직접 인덱싱하는
        # 키들 (det/all_dets/state/hold/want/reason/armed/seq/udp/fps/W/H
        # /healer_coord/healer_map/atk_coord/atk_map/numlock/hwnd_fg/perf/cooldown
        # /preview_frame/preview_offset). snap 에 없는 항목은 안전한 기본값.
        def _g(name, default=None):
            return getattr(snap, name, default)

        # 2026-04-25 v1 healer_worker._compute_state_text 1:1 — 한국어 표시.
        # main_window.lbl_fsm.setText(payload["state"].value) 그대로 사용.
        # 우선순위: 정지 > 일시정지 > 맵전환중 > 따라가기만 > 전투중.
        def _state_text() -> str:
            armed = bool(_g("armed", False))
            follow_only = bool(_g("follow_only", False))
            map_paused = bool(_g("map_paused", False))
            udp_active = bool(_g("udp_active", False))
            if not udp_active and not _g("attacker_state"):
                return "정지"
            if not armed:
                return "일시정지"
            if map_paused or bool(_g("f1_pend_active", False)):
                return "맵전환중"
            if follow_only:
                return "따라가기만"
            return "전투중"

        # preview_frame 은 game_region crop 이 우선 (v1 동작 동일).
        # numpy ndarray 는 truth value 불명 → is None 체크.
        _crop = _g("last_crop")
        if _crop is None:
            _crop = _g("last_frame")
        return {
            # 화면/프리뷰
            "preview_frame": _crop,
            "preview_offset": _g("last_crop_origin", _g("last_frame_origin", (0, 0))),
            "W": _g("frame_w", 0),
            "H": _g("frame_h", 0),
            # YOLO 검출
            "det": _g("red_tab_detection"),
            "all_dets": _g("all_detections", []),
            # FSM 상태 — v1 _compute_state_text 의 한국어 결과를 .value 로 노출.
            # state_text 도 같이 expose — status_strip 등이 stub repr 잘못 찍을 위험 차단.
            "state": _StateStub(_state_text()),
            "state_text": _state_text(),
            # 이동
            "hold": _g("current_dir", "-"),
            "want": _g("want_dir", "-"),
            "reason": _g("move_reason", ""),
            "armed": bool(_g("armed", False)),
            # 좌표
            "healer_coord": _g("healer_coord"),
            "healer_map": _g("healer_map", "") or "",
            "atk_coord": _g("attacker_coord"),
            "atk_map": _g("attacker_map", "") or "",
            # UDP / 시퀀스
            "seq": int(_g("attacker_seq", 0) or 0),
            "udp": bool(_g("udp_active", False)),
            # 시스템
            "fps": float(_g("fps", 0.0) or 0.0),
            "numlock": bool(_g("numlock_on", False)),
            "hwnd_fg": bool(_g("hwnd_fg", False)),
            # 성능 (grab, yolo, ocr, total) ms tuple
            "perf": self._perf_tuple() if self._watchers else _g("perf_tuple", (0.0, 0.0, 0.0, 0.0)),
            # cooldown 데이터 (CooldownReading 또는 dict)
            "cooldown": _g("cooldown_reading"),
            # ts (debug)
            "ts": time.monotonic(),
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "emit_count": self._emit_count,
            "err_count": self._err_count,
            "alive": self.is_alive(),
        }

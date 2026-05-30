"""AttackerWorkerV1Facade — v1_compat 분할 (audit 8.1 3단계).

v1_compat.py 의 AttackerWorkerV1Facade 클래스를 별도 모듈로 분리.
v1_compat.py 가 re-export 로 호환 유지.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt5 import QtCore

from ._compat_logger import setup_compat_logger as _setup_compat_logger
from ._compat_helpers import cfg_to_flat_dict as _cfg_to_flat_dict

log = logging.getLogger("src_v2.workers.v1_compat")


class AttackerWorkerV1Facade(QtCore.QObject):
    """v1 AttackerWorker 호환 facade — 내부 AttackerWorkerV2.

    Signals:
        log_msg(str), stat_ready(dict), cooldown_update(dict),
        own_cooldown_update(dict), stopped()

    Methods:
        start, stop, isRunning, wait,
        set_xp_region/clear, set_cooldown_region/clear,
        set_buff_region/clear, set_hp_region/clear, set_mp_region/clear,
        set_hp_max, set_mp_max, latest_hpmp,
        set_own_skill_names, xp_per_hour, get_analytics_snapshot,
        send_control(target_idx, cmd)
    """

    log_msg = QtCore.pyqtSignal(str)
    stat_ready = QtCore.pyqtSignal(dict)
    cooldown_update = QtCore.pyqtSignal(dict)
    own_cooldown_update = QtCore.pyqtSignal(dict)
    stopped = QtCore.pyqtSignal()

    def __init__(self, cfg, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.log, self.log_path = _setup_compat_logger()
        self._running = False
        self._v2 = None
        self._adapters: Dict[str, Any] = {}
        self._regions: Dict[str, Tuple[int, int, int, int]] = {}
        self._own_skill_names: List[str] = []
        # v1 attribute defaults
        self.armed: bool = False
        self.last_fps: float = 0.0
        try:
            self.yolo_conf: float = float(cfg.vision.conf)
        except Exception:
            self.yolo_conf = 0.25
        try:
            self.yolo_imgsz: int = int(getattr(cfg.vision, "imgsz", 640))
        except Exception:
            self.yolo_imgsz = 640

    def isRunning(self) -> bool:  # noqa: N802
        return self._running

    def wait(self, msec: int = 0) -> bool:
        end = time.monotonic() + (msec / 1000.0 if msec else 2.0)
        while self._running and time.monotonic() < end:
            time.sleep(0.05)
        return not self._running

    def start(self) -> None:
        if self._running:
            return
        try:
            self._build_and_start_v2()
            self._running = True
            self._emit_log("[atk-v2] AttackerWorkerV2 시작")
        except Exception as e:  # noqa: BLE001
            self.log.exception("atk v2 start fail: %s", e)
            self._emit_log(f"[atk-v2][!] 시작 실패: {e}")
            self._running = False
            self.stopped.emit()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        # emit thread 정리
        for ev_attr, th_attr in (
            ("_own_cd_stop_evt", "_own_cd_thread"),
            ("_stat_stop_evt", "_stat_thread"),
        ):
            try:
                ev = getattr(self, ev_attr, None)
                th = getattr(self, th_attr, None)
                if ev is not None:
                    ev.set()
                if th is not None and th.is_alive():
                    th.join(timeout=2.0)
            except Exception:
                pass
        try:
            if self._v2 is not None:
                self._v2.stop(timeout=3.0)
        except Exception as e:  # noqa: BLE001
            self.log.warning("atk v2 stop err: %s", e)
        self._v2 = None
        for key in ("udp_sender", "cd_receiver"):
            try:
                obj = self._adapters.get(key)
                if obj is not None and hasattr(obj, "close"):
                    obj.close()
            except Exception:
                pass
        self.stopped.emit()

    def _build_and_start_v2(self) -> None:
        from src_v2.workers.attacker_worker_v2 import (
            AttackerWorkerV2, AttackerConfig,
        )
        from ._compat_attacker_adapters import build_attacker_adapters
        cfg = self.cfg
        # v1_compat 분할 (audit 8.1) — adapter 빌드 helper 위임.
        ad = build_attacker_adapters(
            cfg=cfg,
            log_cb=self._emit_log,
            yolo_imgsz=getattr(self, "yolo_imgsz", None),
            yolo_conf=getattr(self, "yolo_conf", None),
        )
        self._adapters = ad

        acfg = AttackerConfig(
            capture_poll_sec=0.02, yolo_poll_sec=0.05,
            ocr_poll_sec=0.05, hpmp_poll_sec=0.5,
            udp_send_hz=int(getattr(cfg.net, "send_rate_hz", 30) or 30),
        )
        self._v2 = AttackerWorkerV2(
            cfg=acfg,
            grabber=ad.get("grabber"), yolo=ad.get("yolo"),
            ocr=ad.get("ocr"),
            cooldown=ad.get("cooldown"),
            buff=ad.get("buff"),
            hpmp=ad.get("hpmp"),
            xp=ad.get("xp"),  # 2026-04-27 audit 5.4
            udp_sender=ad.get("udp_sender"),
            cd_receiver=ad.get("cd_receiver"),
            f1_key=ad.get("f1_key"),
        )
        # 2026-04-27 격수 본인 스킬 OCR 타겟 주입 (서브클래스 × 승급 스킬).
        try:
            if self._own_skill_names and hasattr(self._v2, "set_own_skill_names"):
                self._v2.set_own_skill_names(self._own_skill_names)
                self._emit_log(
                    f"[atk-v2] own_skill_names 주입 {self._own_skill_names}"
                )
        except Exception:
            self._emit_log("[atk-v2][!] own_skill_names 주입 실패")
        # 2026-04-27 audit 5.3: peers IP 주입 — _handle_cd_report row 매칭용.
        try:
            _peers = list(getattr(cfg.net, "peers", []) or [])
            if hasattr(self._v2, "set_peers"):
                self._v2.set_peers(_peers)
                self._emit_log(f"[atk-v2] peers 주입 {_peers}")
        except Exception:
            self._emit_log("[atk-v2][!] peers 주입 실패")
        # 저장된 영역 반영
        for key, region in list(self._regions.items()):
            self._inject_region_to_v2(key, region)

        # 2026-04-25 bus subscribe — recv.cd_report → cooldown_update.emit (Qt).
        # AttackerWorkerV2._handle_cd_report 가 bus.publish("recv.cd_report", payload).
        # EventBus handler 시그니처: fn(Event) → payload는 evt.payload.
        try:
            def _on_recv_cd(evt):
                try:
                    payload = getattr(evt, "payload", evt) or {}
                    self.cooldown_update.emit(dict(payload))
                except Exception:
                    pass
            self._v2.bus.subscribe("recv.cd_report", _on_recv_cd)
        except Exception:
            self.log.exception("recv.cd_report subscribe fail")

        # 격수 own cooldown OCR 결과 emit — 1Hz period 로 store 의 cooldown_reading
        # 을 own_cooldown_update.emit 으로 보냄. v1 attacker_worker.py:375-379 1:1.
        self._own_cd_stop_evt = threading.Event()

        def _own_cd_loop():
            import time as _t
            while not self._own_cd_stop_evt.wait(1.0):
                try:
                    snap = self._v2.store.read()
                    cr = getattr(snap, "cooldown_reading", None)
                    if cr is None:
                        continue
                    skills = getattr(cr, "skills", None) or {}
                    if skills:
                        try:
                            self.own_cooldown_update.emit(dict(skills))
                        except Exception:
                            pass
                except Exception:
                    pass

        try:
            self._own_cd_thread = threading.Thread(
                target=_own_cd_loop, name="atk_own_cd_emit", daemon=True,
            )
            self._own_cd_thread.start()
        except Exception:
            self.log.exception("own_cd thread spawn fail")

        # stat_ready emit — main_window 가 사용. 1Hz 로 스냅샷 dict 송출.
        self._stat_stop_evt = threading.Event()

        def _stat_loop():
            while not self._stat_stop_evt.wait(1.0):
                try:
                    st = self._v2.stats() or {}
                    try:
                        self.stat_ready.emit(dict(st))
                    except Exception:
                        pass
                except Exception:
                    pass

        try:
            self._stat_thread = threading.Thread(
                target=_stat_loop, name="atk_stat_emit", daemon=True,
            )
            self._stat_thread.start()
        except Exception:
            self.log.exception("stat thread spawn fail")

        self._v2.start()

    def _inject_region_to_v2(self, key: str, region: Tuple[int, int, int, int]) -> None:
        if self._v2 is None:
            return
        try:
            x, y, w, h = region
        except Exception:
            return
        try:
            method_map = {
                "game": "set_game_region",
                "cooldown": "set_cooldown_region",
                "buff": "set_buff_region",
                "xp": "set_xp_region",
                "hp": "set_hp_region",
                "mp": "set_mp_region",
            }
            mname = method_map.get(key)
            if mname is None:
                return
            fn = getattr(self._v2, mname, None)
            if callable(fn):
                fn(int(x), int(y), int(w), int(h))
        except Exception as e:  # noqa: BLE001
            self.log.warning("atk region inject %s err: %s", key, e)

    # ---- region setters ---- #
    def _set_region(self, key, x, y, w, h):
        self._regions[key] = (int(x), int(y), int(w), int(h))
        self._inject_region_to_v2(key, self._regions[key])
        self._emit_log(f"[atk-v2] {key} region 설정 {x},{y},{w},{h}")

    def _clear_region(self, key):
        self._regions.pop(key, None)
        self._emit_log(f"[atk-v2] {key} region 해제")

    def set_xp_region(self, x, y, w, h): self._set_region("xp", x, y, w, h)
    def clear_xp_region(self): self._clear_region("xp")
    def set_cooldown_region(self, x, y, w, h): self._set_region("cooldown", x, y, w, h)
    def clear_cooldown_region(self): self._clear_region("cooldown")
    def set_buff_region(self, x, y, w, h): self._set_region("buff", x, y, w, h)
    def clear_buff_region(self): self._clear_region("buff")
    def set_hp_region(self, x, y, w, h): self._set_region("hp", x, y, w, h)
    def clear_hp_region(self): self._clear_region("hp")
    def set_mp_region(self, x, y, w, h): self._set_region("mp", x, y, w, h)
    def clear_mp_region(self): self._clear_region("mp")

    def set_hp_max(self, n: int) -> None:
        ad = self._adapters.get("hpmp")
        if ad is not None and hasattr(ad, "set_hp_max"):
            try: ad.set_hp_max(int(n))
            except Exception: pass

    def set_mp_max(self, n: int) -> None:
        ad = self._adapters.get("hpmp")
        if ad is not None and hasattr(ad, "set_mp_max"):
            try: ad.set_mp_max(int(n))
            except Exception: pass

    def latest_hpmp(self):
        ad = self._adapters.get("hpmp")
        if ad is not None and hasattr(ad, "latest"):
            try: return ad.latest()
            except Exception: pass
        return None

    def set_own_skill_names(self, names) -> None:
        try:
            self._own_skill_names = list(names or [])
            # 2026-04-27 누락 수정: healer facade 와 비대칭이었음 — _v2 에 forward
            # 안 해서 격수 cooldown OCR 가 default (healer 스킬: 파력무참/백호의희원)
            # 추적 → [CD-OCR] slot=cd first read {파력무참=-1, 백호의희원=-1, ...}
            if self._v2 is not None and hasattr(self._v2, "set_own_skill_names"):
                self._v2.set_own_skill_names(self._own_skill_names)
        except Exception:
            if not hasattr(self, "_own_skill_names"):
                self._own_skill_names = []

    def xp_per_hour(self) -> int:
        return 0

    def get_analytics_snapshot(self) -> dict:
        if self._v2 is None:
            return {}
        try:
            return self._v2.stats() or {}
        except Exception:
            return {}

    def send_control(self, target_idx: int, cmd: str) -> bool:
        """격수 → 힐러 제어 명령 송신. v1 attacker_worker.send_control 1:1.

        target_idx: -1=전체, 그 외=cfg.net.peers 인덱스.
        cmd: "start" | "pause" | "stop" | "follow_on" | "follow_off" | "ping".

        2026-04-27 BUG-FIX: 이전 버전은 sender.send_control() 위임만 시도했는데
        RealUdpSenderAdapter 에 send_control 메서드가 없어 항상 False 반환 →
        격수가 힐러로 명령 송신 자체가 안 됨 → 힐러 워커가 ARM OFF 그대로 →
        백호/파력/파혼 등 모든 스킬 시전 안 됨.
        """
        try:
            sender = self._adapters.get("udp_sender")
            if sender is None:
                self._emit_log("[CTRL-SEND] 송신 불가 — udp_sender 없음")
                return False
            # 호환: sender 가 자체 send_control 가지면 그걸로.
            if hasattr(sender, "send_control"):
                return bool(sender.send_control(target_idx, cmd))
            # v1 1:1 fallback — RealUdpSenderAdapter._sender (UdpSender) 의 send_to 직접.
            underlying = getattr(sender, "_sender", None)
            if underlying is None or not hasattr(underlying, "send_to"):
                self._emit_log("[CTRL-SEND] 송신 불가 — UdpSender.send_to 없음")
                return False
            from src.net.protocol import ControlCmd, now_ms  # type: ignore
            # v1 동일: target_idx=-1 unicast 로 보내고 수신측이 자기 패킷 처리.
            c = ControlCmd(target_idx=-1, cmd=str(cmd), ts_ms=now_ms())
            data = c.to_bytes()
            peers = list(getattr(self.cfg.net, "peers", []) or [])
            port = int(getattr(self.cfg.net, "port", 51900) or 51900)
            ok_any = False
            if int(target_idx) == -1:
                for p in peers:
                    if underlying.send_to(p, port, data):
                        ok_any = True
            else:
                ti = int(target_idx)
                if 0 <= ti < len(peers):
                    ok_any = bool(underlying.send_to(peers[ti], port, data))
            self._emit_log(
                f"[CTRL-SEND] target={target_idx} cmd={cmd} ok={ok_any} "
                f"peers={peers} port={port}"
            )
            return ok_any
        except Exception as e:  # noqa: BLE001
            self.log.warning("send_control err: %s", e)
            self._emit_log(f"[CTRL-SEND] 예외: {e}")
            return False

    def _emit_log(self, msg: str) -> None:
        try: self.log.info(msg)
        except Exception: pass
        try: self.log_msg.emit(str(msg))
        except Exception: pass



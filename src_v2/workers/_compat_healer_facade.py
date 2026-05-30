"""HealerWorkerV1Facade — v1_compat 분할 (audit 8.1 3단계).

기존 v1_compat.py 의 HealerWorkerV1Facade 클래스를 별도 모듈로 분리.
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


class HealerWorkerV1Facade(QtCore.QObject):
    """v1 HealerWorker 호환 facade — 내부에 HealerWorkerV2 보유.

    main_window 는 본 클래스를 src.workers.healer_worker.HealerWorker 와
    동일하게 다룰 수 있어야 합니다. 외부 노출 인터페이스 전수:

    Signals (v1 동일):
        frame_ready(dict), log_msg(str), stopped(), remote_control_applied(bool, str)

    Methods:
        start(), stop(), isRunning(), wait(ms),
        set_cooldown_region/clear_cooldown_region,
        set_nick_region/clear_nick_region,
        set_buff_region/clear_buff_region,
        set_chat_region/clear_chat_region,
        set_game_region/clear_game_region,
        set_xp_region/clear_xp_region,
        set_hp_region/clear_hp_region,
        set_mp_region/clear_mp_region,
        set_hp_max(n), set_mp_max(n), latest_hpmp(),
        apply_remote_control(cmd),
        set_skill_enabled(name, on),
        set_primary_vk(idx, vk), set_cycle_vks(vks),
        set_skill_vk(name, vk), set_parlyuk_offset(sec),
        set_own_skill_names(names),
        send_control(target_idx, cmd),
        get_analytics_snapshot()

    Attributes (read/write):
        armed, follow_only, min_w, min_h, coord_tol,
        yolo_conf, yolo_every_n, yolo_imgsz, preview_hz_limit,
        ocr_poll_sec, crop_capture_to_game,
        skill_enabled, parlyuk_offset, primary_vks, skill_vks,
        self_heal_hp_thr, gyoungryeok_mp_thr,
        log_path, last_fps
    """

    # ---- v1 호환 signals ----
    frame_ready = QtCore.pyqtSignal(dict)
    log_msg = QtCore.pyqtSignal(str)
    stopped = QtCore.pyqtSignal()
    remote_control_applied = QtCore.pyqtSignal(bool, str)
    # 격수가 보낸 ControlCmd (start/pause/stop/follow_*) 를 GUI 스레드로 라우팅.
    # main_window 가 _handle_remote_cmd 에 connect.
    cmd_received = QtCore.pyqtSignal(str, int)  # (cmd, target_idx)

    def __init__(self, cfg, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.log, self.log_path = _setup_compat_logger()
        self._running = False
        self._stop_evt = threading.Event()
        self._v2 = None  # HealerWorkerV2 (lazy: start() 시 빌드)
        self._adapters: Dict[str, Any] = {}

        # ---- v1 attributes (default values — main_window 가 start() 전 setattr) ----
        # armed/follow_only 는 property 로 v2 store 에 sync (start 후).
        self._armed: bool = False
        self._follow_only: bool = False
        self.min_w: int = 25
        self.min_h: int = 40
        self.coord_tol: int = 1
        try:
            self.yolo_conf: float = float(cfg.vision.conf)
        except Exception:
            self.yolo_conf = 0.25
        self.yolo_every_n: int = 1
        try:
            self.yolo_imgsz: int = int(getattr(cfg.vision, "imgsz", 640))
        except Exception:
            self.yolo_imgsz = 640
        self.preview_hz_limit: float = 0.0
        self.ocr_poll_sec: float = 0.0
        self.crop_capture_to_game: bool = False
        # last_fps/healer_coord/healer_map 는 property — v2 store 에서 read.

        # skill_enabled / skill_vks / primary_vks 는 property — setter 시 v2 sync.
        self._skill_enabled: dict = {
            "백호의희원": True, "백호의희원첨": True,
            "공력증강": True, "부활": True, "파혼술": True,
            "파력무참": True, "금강불체": False,
            "무장": True, "보호": True, "자힐": True,
        }
        self._parlyuk_offset: float = 0.0
        self._self_heal_hp_thr: int = 50
        self._gyoungryeok_mp_thr: int = 30
        self._primary_vks: List[int] = [0x61, 0x62]
        self._skill_vks: Dict[str, int] = {
            "메인힐": 0x61,
            "백호의희원": 0x64, "백호의희원첨": 0x65,
            "공력증강": 0x63,
            "부활": 0x66,
            "파혼술": 0x67,
            "파력무참": 0x68,
            "금강불체": 0x60,
        }

        # 영역 설정 보관 (v2 wiring 시 cfg 에 합류 + 런타임 setter 호출).
        self._regions: Dict[str, Tuple[int, int, int, int]] = {}
        self._own_skill_names: List[str] = []

    # ----------------------------------------------------------------------- #
    # armed / follow_only — v1 main_window 가 직접 setattr 한다. property 로
    # v2 worker store 에 즉시 sync.
    # ----------------------------------------------------------------------- #
    @property
    def armed(self) -> bool:
        return self._armed

    @armed.setter
    def armed(self, v: bool) -> None:
        self._armed = bool(v)
        if self._v2 is not None:
            try:
                self._v2.set_armed(bool(v))
            except Exception:
                pass

    @property
    def follow_only(self) -> bool:
        return self._follow_only

    @follow_only.setter
    def follow_only(self, v: bool) -> None:
        self._follow_only = bool(v)
        if self._v2 is not None:
            try:
                self._v2.set_follow_only(bool(v))
            except Exception:
                pass

    @property
    def last_fps(self) -> float:
        if self._v2 is not None:
            try:
                return float(self._v2.last_fps)
            except Exception:
                pass
        return 0.0

    @property
    def healer_coord(self):
        if self._v2 is not None:
            try:
                return self._v2.healer_coord
            except Exception:
                pass
        return None

    @property
    def healer_map(self) -> str:
        if self._v2 is not None:
            try:
                return self._v2.healer_map
            except Exception:
                pass
        return ""

    # ---- skill_enabled (dict) — main_window 가 dict 통째로 대입 ----
    @property
    def skill_enabled(self) -> dict:
        return self._skill_enabled

    @skill_enabled.setter
    def skill_enabled(self, v: dict) -> None:
        try:
            self._skill_enabled = dict(v or {})
        except Exception:
            self._skill_enabled = {}
        if self._v2 is not None:
            for n, on in self._skill_enabled.items():
                try:
                    self._v2.set_skill_enabled(n, bool(on))
                except Exception:
                    pass

    # ---- skill_vks (dict) ----
    @property
    def skill_vks(self) -> Dict[str, int]:
        return self._skill_vks

    @skill_vks.setter
    def skill_vks(self, v: Dict[str, int]) -> None:
        try:
            self._skill_vks = {str(k): int(vv) for k, vv in dict(v or {}).items()}
        except Exception:
            self._skill_vks = {}
        if self._v2 is not None:
            for n, vk in self._skill_vks.items():
                try:
                    self._v2.set_skill_vk(n, vk)
                except Exception:
                    pass

    # ---- primary_vks (list) ----
    @property
    def primary_vks(self) -> List[int]:
        return self._primary_vks

    @primary_vks.setter
    def primary_vks(self, v: list) -> None:
        try:
            self._primary_vks = [int(x) for x in (v or [])]
        except Exception:
            self._primary_vks = []
        if self._v2 is not None:
            try:
                self._v2.set_primary_vks(list(self._primary_vks))
            except Exception:
                pass

    # ---- parlyuk_offset / self_heal_hp_thr / gyoungryeok_mp_thr ----
    @property
    def parlyuk_offset(self) -> float:
        return self._parlyuk_offset

    @parlyuk_offset.setter
    def parlyuk_offset(self, v: float) -> None:
        try:
            self._parlyuk_offset = float(v)
        except Exception:
            self._parlyuk_offset = 0.0
        if self._v2 is not None:
            try:
                self._v2.set_parlyuk_offset(self._parlyuk_offset)
            except Exception:
                pass

    @property
    def self_heal_hp_thr(self) -> int:
        return self._self_heal_hp_thr

    @self_heal_hp_thr.setter
    def self_heal_hp_thr(self, v: int) -> None:
        try:
            self._self_heal_hp_thr = int(v)
        except Exception:
            self._self_heal_hp_thr = 50
        if self._v2 is not None:
            try:
                self._v2.set_self_heal_hp_thr(self._self_heal_hp_thr)
            except Exception:
                pass

    @property
    def gyoungryeok_mp_thr(self) -> int:
        return self._gyoungryeok_mp_thr

    @gyoungryeok_mp_thr.setter
    def gyoungryeok_mp_thr(self, v: int) -> None:
        try:
            self._gyoungryeok_mp_thr = int(v)
        except Exception:
            self._gyoungryeok_mp_thr = 30
        if self._v2 is not None:
            try:
                self._v2.set_gyoungryeok_mp_thr(self._gyoungryeok_mp_thr)
            except Exception:
                pass

    # ----------------------------------------------------------------------- #
    # QThread-like API (main_window 호환)
    # ----------------------------------------------------------------------- #
    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return self._running

    def wait(self, msec: int = 0) -> bool:
        # v1 main_window 는 wait(2000) 로 부르고 결과 무시. blocking join 으로 충분.
        end = time.monotonic() + (msec / 1000.0 if msec else 2.0)
        while self._running and time.monotonic() < end:
            time.sleep(0.05)
        return not self._running

    def start(self) -> None:
        """v2 HealerWorkerV2 를 lazy build 후 기동.

        main_window 는 frame_ready/log_msg/stopped 시그널을 .start() 직전
        connect 합니다. emit 콜백을 self.frame_ready.emit 으로 전달해
        UiPublisher → frame_ready signal 통합.
        """
        if self._running:
            return
        try:
            self._build_and_start_v2()
            self._running = True
            self._emit_log("[v2] HealerWorkerV2 시작")
        except Exception as e:  # noqa: BLE001
            self.log.exception("v2 start fail: %s", e)
            self._emit_log(f"[v2][!] 시작 실패: {e}")
            self._running = False
            self.stopped.emit()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_evt.set()
        try:
            if self._v2 is not None:
                self._v2.stop(timeout=3.0)
        except Exception as e:  # noqa: BLE001
            self.log.warning("v2 stop err: %s", e)
        self._v2 = None
        # adapter close (있으면)
        try:
            sender = self._adapters.get("udp_sender")
            if sender is not None and hasattr(sender, "close"):
                sender.close()
        except Exception:
            pass
        try:
            recv = self._adapters.get("udp")
            if recv is not None and hasattr(recv, "close"):
                recv.close()
        except Exception:
            pass
        self._emit_log("[v2] HealerWorkerV2 정지")
        self.stopped.emit()

    # ----------------------------------------------------------------------- #
    # v2 wiring (lazy)
    # ----------------------------------------------------------------------- #
    def _build_and_start_v2(self) -> None:
        # v2 import 는 PyQt5 와의 충돌 회피를 위해 start 시점에 lazy 로 .
        from src_v2.workers.healer_worker_v2 import HealerWorkerV2, HealerConfig
        from src_v2.config.migration_v1_to_v2 import migrate_v1_to_v2

        # cfg → v2 dict 변환
        v1_dict = _cfg_to_flat_dict(self.cfg)
        v2_cfg = migrate_v1_to_v2(v1_dict)
        hcfg = self._build_healer_config(v2_cfg)

        # adapter build
        self._adapters = self._build_adapters()

        # frame_ready emit 콜백 — UiPublisher 가 thread 외에서 emit 호출 →
        # Qt5 자동 queued connection 으로 GUI 스레드 전달.
        def _emit(payload: dict) -> None:
            try:
                self.frame_ready.emit(payload)
            except Exception:  # noqa: BLE001
                pass

        self._v2 = HealerWorkerV2(
            cfg=hcfg,
            grabber=self._adapters.get("grabber"),
            yolo=self._adapters.get("yolo"),
            ocr=self._adapters.get("ocr"),
            cooldown=self._adapters.get("cooldown"),
            buff=self._adapters.get("buff"),
            chat=self._adapters.get("chat"),
            hpmp=self._adapters.get("hpmp"),
            xp=self._adapters.get("xp"),
            udp=self._adapters.get("udp"),
            keys=self._adapters.get("keys"),
            uplink_sender=self._adapters.get("uplink_sender"),
            emit_callback=_emit,
            # [CYCLE]/[CD-OCR]/[STARTUP-S]/[UDP-RECV] 등 진단 로그 라우팅.
            # facade _emit_log → file logger + GUI log_msg signal.
            log_callback=self._emit_log,
        )

        # 2026-04-25 store 에 src_idx / 임계치 등 cooldown_uplink._build_payload
        # 가 read 하는 필드를 미리 채움 (적어도 최초 1Hz 송신부터 의미있는 값).
        try:
            _idx = int(getattr(self.cfg.net, "healer_idx", 0) or 0)
            self._v2.store.update(
                src_idx=_idx,
                nickname="",
                xp_per_hour=0,
                event_text="",
                self_heal_hp_thr=int(self.self_heal_hp_thr),
                gyoungryeok_mp_thr=int(self.gyoungryeok_mp_thr),
            )
        except Exception:
            pass

        # main_window 가 start() 전에 setattr 한 값들을 v2 rule_cfg 에 반영.
        try:
            hcfg.rule_cfg["self_heal_hp_thr"] = int(self.self_heal_hp_thr)
            hcfg.rule_cfg["gyoungryeok_mp_thr"] = int(self.gyoungryeok_mp_thr)
        except Exception:
            pass

        # 저장된 영역들도 v2 worker 에 반영 (있는 watcher 만).
        for key, region in list(self._regions.items()):
            self._inject_region_to_v2(key, region)

        # cfg.yaml 자동 영역 (HUD 쿨/버프/HP/MP/맵/XP) — start 시 자동 주입.
        self._auto_inject_cfg_regions()

        # 2026-04-27 BUG-FIX: cfg setter (skill_enabled/parlyuk_offset 등) 를
        # _v2.start() **이전에** 호출. 이전엔 start 후 sync → rule_engine 가
        # default rule_cfg (다 enabled=True) 로 평가 → 체크 해제한 스킬도 fire.
        try:
            self._v2.set_armed(self._armed)
            self._v2.set_follow_only(self._follow_only)
            self._v2.set_self_heal_hp_thr(int(self.self_heal_hp_thr))
            self._v2.set_gyoungryeok_mp_thr(int(self.gyoungryeok_mp_thr))
            self._v2.set_parlyuk_offset(float(self.parlyuk_offset))
            self._v2.set_primary_vks(list(self.primary_vks))
            for _name, _vk in dict(self.skill_vks).items():
                self._v2.set_skill_vk(_name, int(_vk))
            for _name, _on in dict(self.skill_enabled).items():
                self._v2.set_skill_enabled(_name, bool(_on))
            if self._own_skill_names:
                self._v2.set_own_skill_names(self._own_skill_names)
        except Exception as _e:
            self._emit_log(f"[v2] pre-start cfg sync 예외: {_e}")

        self._v2.start()

        # start 후 hp_max/mp_max — adapter 는 v2 init 시점에 이미 wiring 됨.
        try:
            # 2026-04-25 hp_max/mp_max pending 적용 (start 전 set 호출 호환).
            _hpm = int(getattr(self, "_pending_hp_max", 0) or 0)
            _mpm = int(getattr(self, "_pending_mp_max", 0) or 0)
            if _hpm > 0:
                _ad = self._adapters.get("hpmp")
                if _ad is not None and hasattr(_ad, "set_hp_max"):
                    _ad.set_hp_max(_hpm)
                    self._emit_log(f"[v2] hp_max 적용 {_hpm}")
            if _mpm > 0:
                _ad = self._adapters.get("hpmp")
                if _ad is not None and hasattr(_ad, "set_mp_max"):
                    _ad.set_mp_max(_mpm)
                    self._emit_log(f"[v2] mp_max 적용 {_mpm}")
        except Exception:
            self.log.exception("post-start v2 attr sync fail")

    def _auto_inject_cfg_regions(self) -> None:
        """cfg 객체 (project loader load()) 의 cooldown/hp/mp/buff/chat/xp 좌표를
        watcher 에 자동 주입. main_window 가 region picker 로 다시 set_xxx_region
        호출 시 _set_region 이 같은 watcher 에 덮어씀.
        """
        if self._v2 is None:
            return
        try:
            cd = getattr(self.cfg, "cooldown", None)
            if cd is None:
                return
            mappings = [
                ("cooldown", "region_x", "region_y", "region_w", "region_h",
                 "set_cooldown_region"),
                ("buff", "buff_region_x", "buff_region_y", "buff_region_w",
                 "buff_region_h", "set_buff_region"),
                ("chat", "chat_region_x", "chat_region_y", "chat_region_w",
                 "chat_region_h", "set_chat_region"),
                ("hp", "hp_region_x", "hp_region_y", "hp_region_w",
                 "hp_region_h", "set_hp_region"),
                ("mp", "mp_region_x", "mp_region_y", "mp_region_w",
                 "mp_region_h", "set_mp_region"),
                ("xp", "xp_region_x", "xp_region_y", "xp_region_w",
                 "xp_region_h", "set_xp_region"),
                ("nick", "nick_region_x", "nick_region_y", "nick_region_w",
                 "nick_region_h", "set_nick_region"),
                ("game", "game_region_x", "game_region_y", "game_region_w",
                 "game_region_h", "set_game_region"),
            ]
            for key, ax, ay, aw, ah, mname in mappings:
                try:
                    x = int(getattr(cd, ax, -1))
                    w = int(getattr(cd, aw, 0))
                    # 2026-04-25 디버그 로그 — region 값 누락 진단.
                    self._emit_log(f"[v2-cfg] {key} read x={x} w={w}")
                    if x < 0 or w <= 0:
                        continue
                    y = int(getattr(cd, ay, 0))
                    h = int(getattr(cd, ah, 0))
                    fn = getattr(self._v2, mname, None)
                    if callable(fn):
                        fn(x, y, w, h)
                        self._regions[key] = (x, y, w, h)
                except Exception:
                    continue
        except Exception:
            self.log.exception("auto cfg region inject fail")

    def _build_healer_config(self, v2_cfg: Dict[str, Any]):
        from src_v2.workers.healer_worker_v2 import HealerConfig
        eyes = v2_cfg.get("eyes", {})
        rules = v2_cfg.get("rules", {})
        hands = v2_cfg.get("hands", {})
        ui = v2_cfg.get("ui", {})
        mem = v2_cfg.get("memory", {})
        muscle = v2_cfg.get("muscle", {})

        rule_cfg: Dict[str, Any] = {
            "self_heal_hp_thr": int(self.self_heal_hp_thr),
            "self_heal_burst_count": rules.get("self_heal", {}).get("burst_count", 3),
            "self_heal_burst_gap_ms": rules.get("self_heal", {}).get("burst_gap_ms", 80),
            "self_heal_enable_block_b": rules.get("self_heal", {}).get("enable_block_b", True),
            "gyoungryeok_mp_thr": int(self.gyoungryeok_mp_thr),
            "gyoungryeok_enabled": bool(self.skill_enabled.get("공력증강", True)),
            "baekho_enabled": bool(self.skill_enabled.get("백호의희원", True)),
            "parlyuk_enabled": bool(self.skill_enabled.get("파력무참", True)),
            "parhon_edge_sec": rules.get("parhon", {}).get("edge_sec", 3),
            "seq_rclick_enabled": rules.get("seq_rclick", {}).get("enabled", True),
            "seq_rclick_duration_ms": rules.get("seq_rclick", {}).get("duration_ms", 1500),
            "seq_rclick_interval_ms": rules.get("seq_rclick", {}).get("interval_ms", 500),
            "tab_lock_enabled": rules.get("tab_lock", {}).get("enabled", True),
            "combat_band": muscle.get("combat_band", 2),
        }
        return HealerConfig(
            capture_poll_sec=eyes.get("capture_poll_sec", 0.02),
            yolo_poll_sec=eyes.get("yolo", {}).get("poll_sec", 0.05),
            ocr_poll_sec=eyes.get("ocr", {}).get("poll_sec", 0.05),
            cooldown_poll_sec=eyes.get("cooldown", {}).get("poll_sec", 1.0),
            buff_poll_sec=eyes.get("buff", {}).get("poll_sec", 1.0),
            hpmp_poll_sec=eyes.get("hpmp", {}).get("poll_sec", 0.5),
            udp_poll_sec=eyes.get("udp", {}).get("poll_sec", 0.02),
            main_hz_cap=muscle.get("main_loop_hz_cap", 200),
            combat_band=muscle.get("combat_band", 2),
            numlock_enabled=hands.get("numlock", {}).get("enabled", False),
            numlock_interval_sec=hands.get("numlock", {}).get("interval_sec", 30.0),
            ui_publish_hz=ui.get("publish_hz", 15),
            action_log_capacity=mem.get("action_log_capacity", 4096),
            action_log_file=mem.get("action_log_file"),
            rule_cfg=rule_cfg,
        )

    def _build_adapters(self) -> Dict[str, Any]:
        """adapter 인스턴스화. v1_compat 분할 (audit 8.1) — helper 위임."""
        from ._compat_healer_adapters import build_healer_adapters
        return build_healer_adapters(
            cfg=self.cfg,
            log_cb=self._emit_log,
            yolo_imgsz=getattr(self, "yolo_imgsz", None),
            yolo_conf=getattr(self, "yolo_conf", None),
            cmd_emit=lambda c, t: self.cmd_received.emit(c, t),
        )

    def _inject_region_to_v2(self, key: str, region: Tuple[int, int, int, int]) -> None:
        """저장된 영역을 살아있는 v2 worker 의 set_xxx_region setter 로 라우팅.

        adapter 에 직접 호출하지 않고 worker.set_xxx_region 를 거치는 이유:
        worker 가 watcher.adapter chain 을 알고 있어 fallback 처리 일관됨.
        """
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
                "chat": "set_chat_region",
                "xp": "set_xp_region",
                "hp": "set_hp_region",
                "mp": "set_mp_region",
                "nick": "set_nick_region",
            }
            mname = method_map.get(key)
            if mname is None:
                return
            fn = getattr(self._v2, mname, None)
            if callable(fn):
                fn(int(x), int(y), int(w), int(h))
            else:
                self.log.debug("v2 set_%s_region 미존재 — skip", key)
        except Exception as e:  # noqa: BLE001
            self.log.warning("region inject %s err: %s", key, e)

    # ----------------------------------------------------------------------- #
    # v1 region setters — 모두 보관 + 가능 시 v2 즉시 반영
    # ----------------------------------------------------------------------- #
    def _set_region(self, key: str, x: int, y: int, w: int, h: int) -> None:
        self._regions[key] = (int(x), int(y), int(w), int(h))
        self._inject_region_to_v2(key, self._regions[key])
        self._emit_log(f"[v2] {key} region 설정 x={x} y={y} w={w} h={h}")

    def _clear_region(self, key: str) -> None:
        self._regions.pop(key, None)
        self._emit_log(f"[v2] {key} region 해제")

    def set_cooldown_region(self, x, y, w, h): self._set_region("cooldown", x, y, w, h)
    def clear_cooldown_region(self): self._clear_region("cooldown")
    def set_nick_region(self, x, y, w, h): self._set_region("nick", x, y, w, h)
    def clear_nick_region(self): self._clear_region("nick")
    def set_buff_region(self, x, y, w, h): self._set_region("buff", x, y, w, h)
    def clear_buff_region(self): self._clear_region("buff")
    def set_chat_region(self, x, y, w, h): self._set_region("chat", x, y, w, h)
    def clear_chat_region(self): self._clear_region("chat")
    def set_game_region(self, x, y, w, h): self._set_region("game", x, y, w, h)
    def clear_game_region(self): self._clear_region("game")
    def set_xp_region(self, x, y, w, h): self._set_region("xp", x, y, w, h)
    def clear_xp_region(self): self._clear_region("xp")
    def set_hp_region(self, x, y, w, h): self._set_region("hp", x, y, w, h)
    def clear_hp_region(self): self._clear_region("hp")
    def set_mp_region(self, x, y, w, h): self._set_region("mp", x, y, w, h)
    def clear_mp_region(self): self._clear_region("mp")

    def set_hp_max(self, n: int) -> None:
        # 2026-04-25 lazy build 대응 — start 전 호출되면 pending 보관 후 build 끝에 적용.
        try:
            self._pending_hp_max = int(n)
        except Exception:
            self._pending_hp_max = 0
        try:
            ad = self._adapters.get("hpmp")
            if ad is not None and hasattr(ad, "set_hp_max"):
                ad.set_hp_max(int(n))
        except Exception:
            pass

    def set_mp_max(self, n: int) -> None:
        try:
            self._pending_mp_max = int(n)
        except Exception:
            self._pending_mp_max = 0
        try:
            ad = self._adapters.get("hpmp")
            if ad is not None and hasattr(ad, "set_mp_max"):
                ad.set_mp_max(int(n))
        except Exception:
            pass

    def latest_hpmp(self):
        try:
            ad = self._adapters.get("hpmp")
            if ad is not None and hasattr(ad, "latest"):
                return ad.latest()
        except Exception:
            pass
        return None

    # ----------------------------------------------------------------------- #
    # v1 skill setters — 보관만 (v2 rule_cfg 반영은 start 시 + 런타임 setattr).
    # ----------------------------------------------------------------------- #
    def set_skill_enabled(self, name: str, on: bool) -> None:
        self.skill_enabled[name] = bool(on)
        if self._v2 is not None:
            try:
                self._v2.set_skill_enabled(name, bool(on))
            except Exception:
                pass

    def set_skill_vk(self, name: str, vk: int) -> None:
        self.skill_vks[name] = int(vk)
        if self._v2 is not None:
            try:
                self._v2.set_skill_vk(name, int(vk))
            except Exception:
                pass

    def set_primary_vk(self, idx: int, vk: int) -> None:
        try:
            while len(self.primary_vks) <= idx:
                self.primary_vks.append(0)
            self.primary_vks[idx] = int(vk)
            if self._v2 is not None:
                self._v2.set_primary_vks(list(self.primary_vks))
        except Exception:
            pass

    def set_cycle_vks(self, vks: list) -> None:
        try:
            self.primary_vks = [int(v) for v in vks]
            if self._v2 is not None:
                self._v2.set_primary_vks(list(self.primary_vks))
        except Exception:
            pass

    def set_parlyuk_offset(self, sec: float) -> None:
        try:
            self.parlyuk_offset = float(sec)
            if self._v2 is not None:
                self._v2.set_parlyuk_offset(float(sec))
        except Exception:
            pass

    def set_own_skill_names(self, names) -> None:
        try:
            self._own_skill_names = list(names or [])
            if self._v2 is not None:
                self._v2.set_own_skill_names(self._own_skill_names)
        except Exception:
            self._own_skill_names = []

    # ----------------------------------------------------------------------- #
    # v1 misc — main_window 가 호출
    # ----------------------------------------------------------------------- #
    def apply_remote_control(self, cmd: str) -> None:
        c = str(cmd or "").lower()
        on = c == "start"
        if c == "start":
            self.armed = True
            if not self._running:
                self.start()
        elif c == "pause":
            self.armed = False
        elif c == "stop":
            if self._running:
                self.stop()
        elif c == "follow_on":
            self.follow_only = True
        elif c == "follow_off":
            self.follow_only = False
        try:
            self.remote_control_applied.emit(bool(on), c)
        except Exception:
            pass

    def send_control(self, target_idx: int, cmd: str) -> bool:
        """격수 모드에서만 의미 있음. 힐러 facade 에서는 no-op + False."""
        self._emit_log(
            f"[v2] send_control(target={target_idx}, cmd={cmd}) — healer facade no-op"
        )
        return False

    def set_attacker_addr(self, ip: str, port: Optional[int] = None) -> None:
        """ControlListener 가 학습한 격수 IP 를 uplink_sender 에 전달.

        v1 healer_worker.send_uplink 가 recv.last_src_addr() 로 동적 학습한
        격수 IP 로 send_to. v2 의 _UplinkSenderShim.set_attacker_addr 가 동치.
        """
        try:
            sender = self._adapters.get("uplink_sender") if self._adapters else None
            if sender is not None and hasattr(sender, "set_attacker_addr"):
                sender.set_attacker_addr(str(ip), port)
                self._emit_log(
                    f"[v2] uplink_sender 격수 IP 학습 {ip}"
                    + (f":{port}" if port else "")
                )
        except Exception as e:  # noqa: BLE001
            self._emit_log(f"[v2] set_attacker_addr 예외: {e}")

    def get_analytics_snapshot(self) -> dict:
        if self._v2 is None:
            return {}
        try:
            return self._v2.stats() or {}
        except Exception:
            return {}

    # ----------------------------------------------------------------------- #
    # internal
    # ----------------------------------------------------------------------- #
    def _emit_log(self, msg: str) -> None:
        try:
            self.log.info(msg)
        except Exception:
            pass
        try:
            self.log_msg.emit(str(msg))
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# AttackerWorker v1-compat facade
# --------------------------------------------------------------------------- #

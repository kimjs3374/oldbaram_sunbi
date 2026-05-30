from __future__ import annotations
import ctypes
import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from ..utils.logger_setup import _setup_logger
from ..utils.win_helpers import _user32, _is_fg_hwnd, frame_to_qpix

from ..app.attacker import Attacker


class AttackerWorker(QtCore.QThread):
    """격수 역할 QThread 래퍼. attacker.Attacker를 감싸 로그/상태 시그널 emit.

    v5 확장: CooldownReceiver로 힐러들의 쿨다운 역수신. 제어 송신 API 추가.
    """
    log_msg = QtCore.pyqtSignal(str)
    stat_ready = QtCore.pyqtSignal(dict)
    cooldown_update = QtCore.pyqtSignal(dict)  # {idx: CooldownReport} 최신.
    own_cooldown_update = QtCore.pyqtSignal(dict)  # {skill_name: remaining_sec}
    stopped = QtCore.pyqtSignal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        # 2026-04-21: role="attacker" → logs/attacker_*.log 로 저장.
        # 힐러 로그와 구분 + 격수 OCR 진단용.
        self.log, self.log_path = _setup_logger("attacker")
        self._stop = False
        self._app: Attacker = None
        self._cd_recv = None  # CooldownReceiver (격수측).
        # 격수 XP 영역 — run 개시 전에 지정되면 pending에 저장, Attacker 생성 후 적용.
        self._pending_xp_region: Optional[Tuple[int, int, int, int]] = None
        self._xp_region_cleared: bool = False
        # 격수 본인 쿨 영역 + 스킬 리스트 — pending 저장 후 Attacker 생성 시 적용.
        self._pending_cd_region: Optional[Tuple[int, int, int, int]] = None
        self._cd_region_cleared: bool = False
        self._pending_cd_skills: Optional[list] = None
        # 격수 버프(혼마술) 영역 pending — Attacker 생성 전 GUI 에서 지정 대비.
        # 힐러/격수 공용 buff_region_* 과 동일 영역 (사용자 2026-04-20).
        self._pending_buff_region: Optional[Tuple[int, int, int, int]] = None
        self._buff_region_cleared: bool = False
        # HP/MP 영역 pending.
        self._pending_hp_region: Optional[Tuple[int, int, int, int]] = None
        self._hp_region_cleared: bool = False
        self._pending_mp_region: Optional[Tuple[int, int, int, int]] = None
        self._mp_region_cleared: bool = False
        # HP/MP max pending — 사용자 입력 최대값 (OCR cur+max 분리 + pct 환산).
        self._pending_hp_max: int = 0
        self._pending_mp_max: int = 0

    def stop(self):
        self._stop = True
        if self._app:
            self._app.stop()
        if self._cd_recv:
            try:
                self._cd_recv.stop()
            except Exception:
                pass

    # ---- 격수 XP 영역 API ----
    def set_xp_region(self, x: int, y: int, w: int, h: int) -> None:
        self._pending_xp_region = (int(x), int(y), int(w), int(h))
        self._xp_region_cleared = False
        if self._app is not None:
            try:
                self._app.set_xp_region(int(x), int(y), int(w), int(h))
            except Exception as e:
                self.log.warning(f"[xp] set_xp_region 적용 실패: {e}")

    def clear_xp_region(self) -> None:
        self._pending_xp_region = None
        self._xp_region_cleared = True
        if self._app is not None:
            try:
                self._app.clear_xp_region()
            except Exception:
                pass

    # ---- 격수 본인 쿨 OCR API ----
    def set_cooldown_region(self, x: int, y: int, w: int, h: int) -> None:
        self._pending_cd_region = (int(x), int(y), int(w), int(h))
        self._cd_region_cleared = False
        if self._app is not None:
            try:
                self._app.set_cd_region(int(x), int(y), int(w), int(h))
            except Exception as e:
                self.log.warning(f"[cd] set_region 적용 실패: {e}")

    def clear_cooldown_region(self) -> None:
        self._pending_cd_region = None
        self._cd_region_cleared = True
        if self._app is not None:
            try:
                self._app.clear_cd_region()
            except Exception:
                pass

    # ---- 격수 버프 영역 API (힐러와 이름 공용. 혼마술 감시용) ----
    def set_buff_region(self, x: int, y: int, w: int, h: int) -> None:
        self._pending_buff_region = (int(x), int(y), int(w), int(h))
        self._buff_region_cleared = False
        if self._app is not None:
            try:
                self._app.set_buff_region(int(x), int(y), int(w), int(h))
            except Exception as e:
                self.log.warning(f"[buff] set_region 적용 실패: {e}")

    def clear_buff_region(self) -> None:
        self._pending_buff_region = None
        self._buff_region_cleared = True
        if self._app is not None:
            try:
                self._app.clear_buff_region()
            except Exception:
                pass

    # ---- 격수 HP/MP 영역 API ----
    def set_hp_region(self, x: int, y: int, w: int, h: int) -> None:
        self._pending_hp_region = (int(x), int(y), int(w), int(h))
        self._hp_region_cleared = False
        if self._app is not None:
            try:
                self._app.set_hp_region(int(x), int(y), int(w), int(h))
            except Exception as e:
                self.log.warning(f"[hp] set_region 적용 실패: {e}")

    def clear_hp_region(self) -> None:
        self._pending_hp_region = None
        self._hp_region_cleared = True
        if self._app is not None:
            try:
                self._app.clear_hp_region()
            except Exception:
                pass

    def set_mp_region(self, x: int, y: int, w: int, h: int) -> None:
        self._pending_mp_region = (int(x), int(y), int(w), int(h))
        self._mp_region_cleared = False
        if self._app is not None:
            try:
                self._app.set_mp_region(int(x), int(y), int(w), int(h))
            except Exception as e:
                self.log.warning(f"[mp] set_region 적용 실패: {e}")

    def clear_mp_region(self) -> None:
        self._pending_mp_region = None
        self._mp_region_cleared = True
        if self._app is not None:
            try:
                self._app.clear_mp_region()
            except Exception:
                pass

    def set_hp_max(self, n: int) -> None:
        """격수 자신의 최대 HP (OCR cur+max 분리용). pending 으로 보관."""
        self._pending_hp_max = int(n)
        if self._app is not None:
            try:
                self._app.set_hp_max(int(n))
            except Exception as e:
                self.log.warning(f"[hp] set_max 적용 실패: {e}")

    def set_mp_max(self, n: int) -> None:
        self._pending_mp_max = int(n)
        if self._app is not None:
            try:
                self._app.set_mp_max(int(n))
            except Exception as e:
                self.log.warning(f"[mp] set_max 적용 실패: {e}")

    def latest_hpmp(self):
        """테스트 버튼용 — 최근 HP/MP 관측값."""
        if self._app is None:
            from ..vision.hpmp import HpMp
            return HpMp(hp=-1, mp=-1)
        try:
            return self._app.latest_hpmp()
        except Exception:
            from ..vision.hpmp import HpMp
            return HpMp(hp=-1, mp=-1)

    def set_own_skill_names(self, names) -> None:
        """격수 서브클래스 스킬 리스트 (List[str]) 주입."""
        self._pending_cd_skills = list(names or [])
        if self._app is not None:
            try:
                self._app.set_cd_skills(list(names or []))
            except Exception as e:
                self.log.warning(f"[cd] set_skills 적용 실패: {e}")

    def xp_per_hour(self) -> int:
        if self._app is None:
            return 0
        try:
            return int(self._app.get_xp_per_hour())
        except Exception:
            return 0

    def get_analytics_snapshot(self) -> dict:
        if self._app is None:
            return {}
        try:
            return self._app.get_analytics_snapshot()
        except Exception:
            return {}

    def send_control(self, target_idx: int, cmd: str) -> bool:
        """격수 → 힐러(들) 제어 명령 송신.

        target_idx: -1=전체, 그 외=cfg.net.peers 인덱스.
        cmd: "start" | "pause" | "stop".
        """
        if self._app is None or self._app.sender is None:
            self.log_msg.emit("[CTRL] 송신 불가 — attacker 미시작")
            return False
        try:
            from ..net.protocol import ControlCmd, now_ms
            # 개별/전체 모두 unicast + target_idx=-1 로 송신하면 수신 PC는
            # healer_idx 설정과 무관하게 자기가 받은 패킷을 처리함.
            c = ControlCmd(target_idx=-1, cmd=str(cmd), ts_ms=now_ms())
            data = c.to_bytes()
            peers = list(self.cfg.net.peers)
            port = int(self.cfg.net.port)
            ok_any = False
            if target_idx == -1:
                for p in peers:
                    if self._app.sender.send_to(p, port, data):
                        ok_any = True
            else:
                if 0 <= target_idx < len(peers):
                    ok_any = self._app.sender.send_to(
                        peers[target_idx], port, data
                    )
            self.log.info(
                f"[CTRL-SEND] target={target_idx} cmd={cmd} ok={ok_any} "
                f"peers={peers} port={port}"
            )
            return ok_any
        except Exception as e:
            self.log.exception(f"[CTRL] 송신 실패: {e}")
            return False

    def run(self):
        self.log.info("=== attacker start ===")
        self.log.info(f"log_path={self.log_path}")
        try:
            def _log(s: str):
                self.log_msg.emit(str(s))
                self.log.info(str(s))

            def _stat(d: dict):
                self.stat_ready.emit(d)

            # 쿨다운 역수신 시작.
            try:
                from ..net.udp_receiver import CooldownReceiver
                bind_host = getattr(self.cfg.net, "bind_host", "0.0.0.0")
                recv_port = int(getattr(
                    self.cfg.net, "attacker_recv_port", 45455
                ))
                self._cd_recv = CooldownReceiver(bind_host, recv_port)
                self._cd_recv_seen_idx: set = set()
                cfg_ref = self.cfg

                def _on_cd(rep, src_addr=None):
                    # 수신 스레드에서 호출 → Qt 시그널로 GUI 스레드에 넘김.
                    try:
                        # src_addr IP로 peers 매칭 → 힐러 PC별 고유 행.
                        # 힐러 cfg.net.healer_idx가 두 PC 모두 0으로 설정된
                        # 경우에도 IP로 구분 가능.
                        row_idx = int(getattr(rep, "src_idx", 0))
                        src_ip = ""
                        if (src_addr and isinstance(src_addr, tuple)
                                and src_addr[0]):
                            src_ip = str(src_addr[0])
                            peers = list(getattr(cfg_ref.net, "peers", []))
                            for i, p in enumerate(peers):
                                if str(p).strip() == src_ip:
                                    row_idx = i
                                    break
                        key = (row_idx, src_ip)
                        if key not in self._cd_recv_seen_idx:
                            self._cd_recv_seen_idx.add(key)
                            self.log.info(
                                f"[CD-RECV] first from ip={src_ip} "
                                f"row={row_idx} reported_idx={rep.src_idx} "
                                f"p={rep.cd_parlyuk} b={rep.cd_baekho} "
                                f"armed={getattr(rep,'armed',False)} "
                                f"nick={getattr(rep,'nickname','')!r}"
                            )
                        # 2026-04-23: 주기 진단 로그 — 힐러별 10초/1회.
                        # 파력무참 버프 전송 추적용. first-only 로그로는
                        # 힐러 측에서 buff OCR 이 동작 중인지 확인 불가.
                        try:
                            import time as _t_mod
                            now_s = _t_mod.monotonic()
                            last_map = getattr(self, "_cd_recv_snap_ts", None)
                            if last_map is None:
                                last_map = {}
                                self._cd_recv_snap_ts = last_map
                            last = float(last_map.get(key, 0.0))
                            if now_s - last >= 10.0:
                                self.log.info(
                                    f"[CD-RECV-SNAP] row={row_idx} "
                                    f"ip={src_ip} "
                                    f"p={rep.cd_parlyuk} b={rep.cd_baekho} "
                                    f"buff_parlyuk_sec={int(getattr(rep, 'buff_parlyuk_sec', -1))} "
                                    f"armed={getattr(rep,'armed',False)} "
                                    f"nick={getattr(rep,'nickname','')!r}"
                                )
                                last_map[key] = now_s
                        except Exception:
                            pass
                        self.cooldown_update.emit({
                            "src_idx": row_idx,
                            "reported_idx": int(getattr(rep, "src_idx", 0)),
                            "src_ip": src_ip,
                            "cd_parlyuk": rep.cd_parlyuk,
                            "cd_baekho": rep.cd_baekho,
                            "ts_ms": rep.ts_ms,
                            "armed": bool(getattr(rep, "armed", False)),
                            "nickname": str(getattr(rep, "nickname", "") or ""),
                            "buff_parlyuk_sec": int(getattr(
                                rep, "buff_parlyuk_sec", -1
                            )),
                            "xp_per_hour": int(getattr(
                                rep, "xp_per_hour", 0
                            )),
                            # 2026-04-21: 힐러 이벤트 알림 (공력증강 임박 / 자힐
                            # 하는중). main_window 가 event_seq 증가 감지 시
                            # overlay.push_alert 호출. 이전엔 이 필드 누락해서
                            # 격수 UI 까지 전달 안 되던 버그 수정.
                            "event_text": str(getattr(
                                rep, "event_text", "") or ""),
                            "event_seq": int(getattr(rep, "event_seq", 0)),
                            # 2026-04-22: 힐러 자기 HP/MP (격수 HP/MP 오버레이용).
                            # 누락 시 기본 -1/0 → 오버레이에서 "--" 로 표시됨.
                            "hp_pct": int(getattr(rep, "hp_pct", -1)),
                            "mp_pct": int(getattr(rep, "mp_pct", -1)),
                            "hp_cur": int(getattr(rep, "hp_cur", -1)),
                            "mp_cur": int(getattr(rep, "mp_cur", -1)),
                            "hp_max": int(getattr(rep, "hp_max", 0)),
                            "mp_max": int(getattr(rep, "mp_max", 0)),
                            # 힐러 임계치 (자힐/공증 판정 기준값, 격수 사용).
                            "self_heal_hp_thr": int(getattr(
                                rep, "self_heal_hp_thr", -1)),
                            "gyoungryeok_mp_thr": int(getattr(
                                rep, "gyoungryeok_mp_thr", -1)),
                        })
                    except Exception as _e:
                        try:
                            self.log.info(
                                f"[CD-RECV] emit err "
                                f"{type(_e).__name__}: {_e}"
                            )
                        except Exception:
                            pass
                self._cd_recv.set_report_handler(_on_cd)
                self._cd_recv.start()
                self.log.info(
                    f"[CD-RECV] listen {bind_host}:{recv_port}"
                )
            except Exception as e:
                self.log.warning(f"[CD-RECV] 시작 실패: {e}")

            def _own_cd(skills: dict):
                try:
                    self.own_cooldown_update.emit(dict(skills))
                except Exception:
                    pass

            self._app = Attacker(
                self.cfg, log_cb=_log, stat_cb=_stat, own_cd_cb=_own_cd,
            )
            # pending 상태로 지정된 XP 영역이 있으면 Attacker 생성 직후 적용.
            try:
                if self._pending_xp_region is not None:
                    x, y, w, h = self._pending_xp_region
                    self._app.set_xp_region(x, y, w, h)
                elif self._xp_region_cleared:
                    self._app.clear_xp_region()
            except Exception as _e:
                self.log.warning(f"[xp] pending 적용 실패: {_e}")
            # 격수 본인 쿨 영역/스킬 pending 적용.
            try:
                if self._pending_cd_skills is not None:
                    self._app.set_cd_skills(list(self._pending_cd_skills))
                if self._pending_cd_region is not None:
                    x, y, w, h = self._pending_cd_region
                    self._app.set_cd_region(x, y, w, h)
                elif self._cd_region_cleared:
                    self._app.clear_cd_region()
            except Exception as _e:
                self.log.warning(f"[cd] pending 적용 실패: {_e}")
            # 격수 버프(혼마술) 영역 pending 적용.
            try:
                if self._pending_buff_region is not None:
                    x, y, w, h = self._pending_buff_region
                    self._app.set_buff_region(x, y, w, h)
                elif self._buff_region_cleared:
                    self._app.clear_buff_region()
            except Exception as _e:
                self.log.warning(f"[buff] pending 적용 실패: {_e}")
            # 격수 HP/MP 영역 pending 적용.
            try:
                if self._pending_hp_region is not None:
                    x, y, w, h = self._pending_hp_region
                    self._app.set_hp_region(x, y, w, h)
                elif self._hp_region_cleared:
                    self._app.clear_hp_region()
                if self._pending_mp_region is not None:
                    x, y, w, h = self._pending_mp_region
                    self._app.set_mp_region(x, y, w, h)
                elif self._mp_region_cleared:
                    self._app.clear_mp_region()
            except Exception as _e:
                self.log.warning(f"[hpmp] pending 적용 실패: {_e}")
            # HP/MP max pending 적용.
            try:
                if self._pending_hp_max:
                    self._app.set_hp_max(int(self._pending_hp_max))
                if self._pending_mp_max:
                    self._app.set_mp_max(int(self._pending_mp_max))
            except Exception as _e:
                self.log.warning(f"[hpmp] max pending 적용 실패: {_e}")
            # Attacker.run은 self._stop 루프. stop()이 그걸 잡음.
            self._app.run()
        except Exception as e:
            self.log_msg.emit(f"[attacker 에러] {e}")
            self.log.exception(f"attacker 에러: {e}")
        try:
            if self._cd_recv:
                self._cd_recv.stop()
        except Exception:
            pass
        self.log.info("=== attacker stop ===")
        self.stopped.emit()



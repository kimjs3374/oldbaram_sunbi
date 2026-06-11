"""격수 PC 엔트리. OCR로 자기 좌표/맵 + YOLO로 자기 빨탭을 뽑아
힐러 PC들에 30Hz UDP 송신.

2026-04-22 이전 설계에서는 "격수 빨탭 불필요 → YOLO 미사용"이었으나, 격수 전용
스킬범위 오버레이가 자기 캐릭터 위치(빨탭 중심)를 요구하여 YOLO detect_red 를
활성화. 힐러와 동일 학습 가중치(cfg.vision.weights) 공유.
"""
import argparse
import signal
import time
from collections import deque

from ..config import load as load_cfg
from ..capture.screen import Grabber, AsyncGrabber
from ..vision.ocr import Ocr, AsyncOcr, _is_ocr_noise
from ..vision.xp_ocr import XpOcr
from ..vision.cooldown_ocr import CooldownOcr
from ..net.udp_sender import UdpSender
from ..net.protocol import State, now_ms
from ..input.keys import find_windows_by_process
from .hunt_analytics import HuntAnalytics

try:
    import win32api
    _VK_F1 = 0x70
    _VK_F2 = 0x71
    _HAVE_WIN32 = True
except Exception:
    _HAVE_WIN32 = False


class Attacker:
    def __init__(self, cfg, log_cb=None, stat_cb=None, own_cd_cb=None):
        self.cfg = cfg
        self.log = log_cb if log_cb else print
        self.stat_cb = stat_cb  # dict(st, region) 넘김. GUI 표시용.
        # 격수 본인 스킬 쿨 OCR 결과 콜백 — {skill_name: remaining_sec}.
        self.own_cd_cb = own_cd_cb
        hwnd = None
        if cfg.input.target_window.lower().endswith(".exe"):
            wins = find_windows_by_process(cfg.input.target_window)
            if wins:
                hwnd = wins[0]
                self.log(f"[attacker] 창 캡처 hwnd={hwnd} "
                         f"({cfg.input.target_window})")
            else:
                self.log(f"[attacker][!] {cfg.input.target_window} 창 없음 → "
                         f"monitor_index={cfg.capture.monitor_index} fallback")
        # 2026-04-22: AsyncGrabber — 캡처 스레드 분리로 mss 지연(BitBlt) 이
        # 메인 루프 fps 영향 없게. 힐러와 동일 패턴 (healer_worker:620).
        self.grab = AsyncGrabber(cfg.capture.monitor_index, hwnd=hwnd,
                                 target_interval_s=0.02)
        # 2026-04-21: msw.exe 포커스 체크용 hwnd 저장.
        self._msw_hwnd = hwnd
        self.log(f"[attacker] capture region={self.grab.mon}")
        # 2026-04-21: gpu=True 로 복귀 (CPU 실험 실패).
        self.ocr = Ocr(coord_w=cfg.ocr.coord_w, coord_h=cfg.ocr.coord_h,
                        coord_right_pad=cfg.ocr.coord_right_pad,
                        coord_bottom_pad=cfg.ocr.coord_bottom_pad,
                        coord_upscale=cfg.ocr.coord_upscale,
                        map_w=cfg.ocr.map_w, map_h=cfg.ocr.map_h,
                        map_top_pad=cfg.ocr.map_top_pad,
                        map_left_pad=getattr(cfg.ocr, "map_left_pad", -1),
                        map_upscale=cfg.ocr.map_upscale,
                        gpu=True)
        # AsyncOcr 래퍼 — OCR 을 메인 루프에서 떼어 블로킹 0. 힐러와 동일 패턴.
        self._ocr_async = AsyncOcr(self.ocr)
        self.sender = UdpSender(cfg.net.peers, cfg.net.port)
        # 격수 XP OCR (설정 탭에서 경험치 영역 지정 시 활성).
        # log_cb를 Attacker.log로 연결 → 첫 성공/지속 실패를 외부 로그에서
        # 바로 확인 가능 (gain=0 디버깅 핵심).
        self.xp_ocr = XpOcr(log_cb=self.log)
        # 사냥 분석 — 바퀴/세션/일별 리포트.
        self.analytics = HuntAnalytics()
        # 격수 관측 맵 누적 — ocr.set_known_maps 로 주입하여 OCR 내부 canonical
        # 교정 활성화. 누적 안 하면 raw 깨진 이름("센비족", "선비족24")이
        # UDP + 리포트에 그대로 흘러들어감. 2026-04-19 실증.
        self._observed_maps: set = set()
        self._stop = False
        self._seq = 0
        self._coord_hist = deque(maxlen=6)
        self._last = State()
        # 2026-04-22 B안: 맵 변경 hold 제거. 오염 필터링은 힐러측으로 일원화.
        # (구 self._map_hold_until/_map_hold_sec 제거 — UI "-" 깜빡임 해소)
        self._prev_sent_map = ""
        # 맵전환 이벤트 시퀀스 — 매 맵 변경마다 +1. 힐러는 edge 감지해 pause+재계획.
        # UDP loss 보험으로 전환 직후 N프레임 동안 같은 패킷을 번들 송신.
        self._map_seq = 0
        self._map_burst_remaining = 0
        self._map_burst_n = 3
        # 2026-04-22: 같은 맵 이름 내 좌표 급변(격수 맵 OCR 지연 창) → 워프로 간주하고
        # map_seq++ 송신해 힐러 anchor 리셋. 힐러 J안 점프필터가 1~2초 잠기는 문제
        # (힐러1.txt 16:53:52 재현: ATK-COORD-JUMP 11회 반복) 해소.
        # 임계값은 힐러 J안 thr=8보다 크게 잡아야 함 — 낮으면(예: 8) 격수 정상 이동
        # 10~15칸 튐에도 MAP-SEQ-EDGE 오탐 발동해 힐러 추종 끊김 + TAB-CONFIRM 취소
        # (힐러1.txt 17:07:32/46, 08:00 오탐 재현). 25칸 = 진짜 워프/서브맵 전환 폭.
        self._warp_threshold = 25
        # F1 (2026-06-12 변경): 기존 "맵전환 예고(map_change_pending)" 명령 제거 →
        # 격수가 F1 누르면 힐러에게 "격수 빨탭 재고정 시퀀스 즉시 실행" 트리거.
        # 카운터 증가분을 State.reanchor_seq 로 송신 → 힐러가 증가 감지 시 1회 발동.
        self._reanchor_seq = 0
        # F2 (2026-06-12): 쩔캐(현인) 지폭지술 트리거. 몹이 충분히 모이면 격수가
        # F2 → State.jipok_seq 증가 송신 → 쩔캐가 증가 감지 시 지폭지술 시퀀스.
        self._jipok_seq = 0
        # 좌표급변(=맵전환, 워프 거의 없음) 감지 시 맵이름 OCR 갱신까지
        # map_change_pending 강제 ON → 격수 맵OCR(RapidOCR) 지연을 좌표(0.01초)로 흡수.
        self._map_chg_until = 0.0
        self._f1_prev_down = False
        self._f2_prev_down = False
        # 격수 본인 쿨 OCR — 설정 탭에서 영역/스킬 주입 전까진 inactive.
        self.cd_ocr = CooldownOcr(poll_sec=1.0, name="atk_cd")
        self.cd_ocr.start()
        self._last_own_cd_emit = 0.0
        self._own_cd_emit_period = 1.0  # 초.
        # HP/MP OCR 리더 — 영역 지정 시 OCR 결과(cur/max → pct) 를 State 로 송신.
        from ..vision.hpmp import HpMpReader
        self.hpmp = HpMpReader(log_cb=self.log)
        # cfg 저장 max (있으면 선적용, GUI 재주입도 수용).
        try:
            cd2 = getattr(cfg, "cooldown", None)
            if cd2 is not None:
                self.hpmp.set_hp_max(int(getattr(cd2, "hp_max", 0)))
                self.hpmp.set_mp_max(int(getattr(cd2, "mp_max", 0)))
        except Exception:
            pass
        try:
            cd = getattr(cfg, "cooldown", None)
            hx = int(getattr(cd, "hp_region_x", -1))
            hw = int(getattr(cd, "hp_region_w", 0))
            if hx >= 0 and hw > 0:
                self.hpmp.set_hp_region(
                    hx,
                    int(getattr(cd, "hp_region_y", 0)),
                    hw,
                    int(getattr(cd, "hp_region_h", 0)),
                )
            mx = int(getattr(cd, "mp_region_x", -1))
            mw = int(getattr(cd, "mp_region_w", 0))
            if mx >= 0 and mw > 0:
                self.hpmp.set_mp_region(
                    mx,
                    int(getattr(cd, "mp_region_y", 0)),
                    mw,
                    int(getattr(cd, "mp_region_h", 0)),
                )
        except Exception as _e:
            self.log(f"[attacker][hpmp] cfg region 주입 실패: {_e}")
        # 빨탭 sticky 캐시 — 마법 이펙트/UI 가림 등으로 YOLO 가 순간
        # 놓치는 프레임에서 오버레이가 깜빡이지 않도록 직전 좌표 유지.
        # TTL 내 재검출 실패 시에만 red_tab=False 로 전환.
        self._last_red_cx: int = 0
        self._last_red_cy: int = 0
        self._last_red_ts: float = 0.0
        self._red_ttl_sec: float = 3.0
        self._last_red_box: tuple = (0, 0, 0, 0)

    def stop(self, *_):
        self._stop = True
        try:
            self.xp_ocr.stop()
        except Exception:
            pass
        try:
            self.cd_ocr.stop()
        except Exception:
            pass
        # 2026-04-22: Async 3종(grabber/yolo/ocr) 백그라운드 스레드 정리.
        # 힐러와 동일 (memory: async 3종 stop 누락 → 좀비 스레드 누적).
        try:
            _ya = getattr(self, "_yolo_async", None)
            if _ya is not None:
                _ya.stop()
        except Exception:
            pass
        try:
            _oa = getattr(self, "_ocr_async", None)
            if _oa is not None:
                _oa.stop()
        except Exception:
            pass
        try:
            if hasattr(self.grab, "stop"):
                self.grab.stop()
        except Exception:
            pass
        try:
            rec = self.analytics.stop()
            if rec is not None:
                self.log(
                    f"[HUNT-STOP] duration={rec.duration_sec}s "
                    f"xp_gain={rec.xp_gain} laps={len(rec.laps)} "
                    f"top={rec.map_top}"
                )
        except Exception:
            pass

    def get_analytics_snapshot(self) -> dict:
        try:
            return self.analytics.snapshot()
        except Exception:
            return {}

    def set_xp_region(self, x: int, y: int, w: int, h: int) -> None:
        try:
            self.xp_ocr.set_region(int(x), int(y), int(w), int(h))
            self.xp_ocr.reset()
        except Exception as e:
            self.log(f"[attacker][xp] set_region 실패: {e}")

    def clear_xp_region(self) -> None:
        try:
            self.xp_ocr.clear_region()
            self.xp_ocr.reset()
        except Exception:
            pass

    def get_xp_per_hour(self) -> int:
        try:
            return int(self.xp_ocr.xp_per_hour())
        except Exception:
            return 0

    # ---- 격수 본인 쿨 OCR API ----
    def set_cd_region(self, x: int, y: int, w: int, h: int) -> None:
        try:
            self.cd_ocr.set_region(int(x), int(y), int(w), int(h))
            # 2026-04-23: 성공 시 INFO 로그 (기존엔 실패만 찍혀 "미지정" 오판 유발).
            self.log(
                f"[attacker][cd] region 설정 x={x} y={y} w={w} h={h}"
            )
        except Exception as e:
            self.log(f"[attacker][cd] set_region 실패: {e}")

    def clear_cd_region(self) -> None:
        try:
            self.cd_ocr.clear_region()
            self.log("[attacker][cd] region 해제")
        except Exception:
            pass

    def set_cd_skills(self, names_or_mapping) -> None:
        """격수 서브클래스 스킬 리스트 주입. List[str] 또는 {name: [keywords]}."""
        try:
            self.cd_ocr.set_target_skills(names_or_mapping)
            # 2026-04-23: 성공 시 INFO 로그. 타겟 누락 재현 시 설정 상태 추적용.
            if isinstance(names_or_mapping, dict):
                _names = list(names_or_mapping.keys())
            else:
                _names = list(names_or_mapping or [])
            self.log(f"[attacker][cd] target_skills 설정 names={_names}")
        except Exception as e:
            self.log(f"[attacker][cd] set_target_skills 실패: {e}")

    def latest_own_cds(self) -> dict:
        try:
            r = self.cd_ocr.latest()
            return dict(getattr(r, "skills", {}) or {})
        except Exception:
            return {}

    # ---- HP/MP 영역 API (격수 본인 HP/MP → UDP State 로 송신) ----
    def set_hp_region(self, x: int, y: int, w: int, h: int) -> None:
        try:
            self.hpmp.set_hp_region(int(x), int(y), int(w), int(h))
            self.log(f"[attacker][hp] region 설정 x={x} y={y} w={w} h={h}")
        except Exception as e:
            self.log(f"[attacker][hp] set_region 실패: {e}")

    def clear_hp_region(self) -> None:
        try:
            self.hpmp.clear_hp_region()
        except Exception:
            pass

    def set_mp_region(self, x: int, y: int, w: int, h: int) -> None:
        try:
            self.hpmp.set_mp_region(int(x), int(y), int(w), int(h))
            self.log(f"[attacker][mp] region 설정 x={x} y={y} w={w} h={h}")
        except Exception as e:
            self.log(f"[attacker][mp] set_region 실패: {e}")

    def clear_mp_region(self) -> None:
        try:
            self.hpmp.clear_mp_region()
        except Exception:
            pass

    def set_hp_max(self, n: int) -> None:
        try:
            self.hpmp.set_hp_max(int(n))
            self.log(f"[attacker][hp] max 설정 n={n}")
        except Exception as e:
            self.log(f"[attacker][hp] set_max 실패: {e}")

    def set_mp_max(self, n: int) -> None:
        try:
            self.hpmp.set_mp_max(int(n))
            self.log(f"[attacker][mp] max 설정 n={n}")
        except Exception as e:
            self.log(f"[attacker][mp] set_max 실패: {e}")

    def latest_hpmp(self):
        """최근 관측 HP/MP (0~100 int %, -1=미관측). 테스트 버튼용."""
        try:
            return self.hpmp.latest()
        except Exception:
            from ..vision.hpmp import HpMp
            return HpMp(hp=-1, mp=-1)

    def _dir(self) -> str:
        if len(self._coord_hist) < 2:
            return "-"
        x0, y0 = self._coord_hist[0]
        x1, y1 = self._coord_hist[-1]
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) < 1 and abs(dy) < 1:
            return "-"
        if abs(dx) >= abs(dy):
            return "R" if dx > 0 else "L"
        return "D" if dy > 0 else "U"

    def run(self):
        # 2026-04-22: Windows timer 해상도 1ms 로 올림. 기본 15.625ms tick 이면
        # 60Hz(period=16.7ms) 는 time.sleep 오버슬립으로 실측 ~32fps 상한.
        # timeBeginPeriod(1) 호출 후 ~60Hz 달성 가능. 종료 시 timeEndPeriod 필수.
        # Windows 외 OS 에선 조용히 스킵.
        _timer_period_set = False
        try:
            import ctypes
            if hasattr(ctypes, "windll"):
                _ret = ctypes.windll.winmm.timeBeginPeriod(1)
                if _ret == 0:  # TIMERR_NOERROR
                    _timer_period_set = True
                    self.log("[attacker] timeBeginPeriod(1) OK — 1ms timer")
                else:
                    self.log(f"[attacker] timeBeginPeriod(1) 실패 ret={_ret}")
        except Exception as _e:
            self.log(f"[attacker] timeBeginPeriod skip: {_e}")

        period = 1.0 / self.cfg.net.send_rate_hz
        # 2026-04-23: 루프 pacing 제거(B 옵션). 캡처/YOLO/OCR 은 하드웨어 한계까지
        # 돌고, UDP 송신만 send_period 로 스로틀. period 변수는 PERF 로그용 유지.
        send_period = period
        _last_send_t = 0.0
        _send_count = 0
        every = self.cfg.ocr.ocr_every_n_frames
        frame_i = 0
        self.log(f"[attacker] 시작 loop=uncapped send={self.cfg.net.send_rate_hz}Hz "
                 f"→ {self.cfg.net.peers}:{self.cfg.net.port}")
        last_log = 0.0
        last_raw_c = ""
        last_raw_m = ""
        last_ok = False
        # 2026-04-21: msw.exe 비활성 시 OCR 중지용 헬퍼.
        # 포커스 상태 로그는 1회만 (상태 변화 시).
        _last_fg_ok = None
        # 2026-04-22: 프레임 드랍/YOLO 지연 측정. 1초 단위 집계 후 리셋.
        # period(=1/send_rate_hz) 보다 loop 한 바퀴가 오래 걸리면 sleep<0 →
        # drop_cnt 증가. yolo_sum/yolo_n 으로 평균 추론시간 산출.
        _stats_frames = 0
        _stats_drops = 0
        _stats_over_ms = 0.0
        _stats_loop_ms = 0.0
        _stats_yolo_ms = 0.0
        _stats_yolo_n = 0
        _stats_ocr_ms = 0.0
        _stats_ocr_n = 0

        while not self._stop:
            _t_loop0 = time.perf_counter()
            frame = self.grab.grab()

            # 2026-04-21: msw.exe 창이 포그라운드가 아니면 OCR submit 중지.
            # 다른 창 덮여있거나 최소화된 상태에선 캡처된 frame 이 msw 내용이
            # 아닐 수 있고, 타 창 UI 를 OCR 해 오탐 발생. 포커스 있을 때만 submit.
            try:
                from ..utils.win_helpers import _is_fg_hwnd
                fg_ok = bool(self._msw_hwnd) and _is_fg_hwnd(self._msw_hwnd)
            except Exception:
                fg_ok = True  # 체크 실패 시 보수적으로 허용.
            if fg_ok != _last_fg_ok:
                self.log(
                    f"[attacker] msw 포커스 {_last_fg_ok}→{fg_ok} "
                    f"(OCR {'활성' if fg_ok else '중지'})"
                )
                _last_fg_ok = fg_ok

            # 격수 XP + 본인 쿨 OCR — 영역 지정돼 있을 때만 프레임 넘김 (백그라운드).
            origin = (
                int(self.grab.mon.get("left", 0)),
                int(self.grab.mon.get("top", 0)),
            )
            try:
                if fg_ok and self.cd_ocr.ready():
                    self.cd_ocr.submit_frame(frame, origin)
            except Exception:
                pass
            # 본인 쿨 결과 주기 emit — 초당 1회.
            try:
                now_emit = time.time()
                if (self.own_cd_cb
                        and now_emit - self._last_own_cd_emit
                        >= self._own_cd_emit_period):
                    skills = self.latest_own_cds()
                    if skills:
                        try:
                            self.own_cd_cb(dict(skills))
                        except Exception:
                            pass
                    self._last_own_cd_emit = now_emit
                    # 2026-04-23: 30초당 cd_ocr 진단 스냅샷.
                    _last_cd_diag = getattr(self, "_last_cd_diag_ts", 0.0)
                    if now_emit - _last_cd_diag >= 30.0:
                        try:
                            _rd = self.cd_ocr.latest()
                            _skills_snap = dict(
                                getattr(_rd, "skills", {}) or {}
                            )
                            _raw_snap = str(
                                getattr(_rd, "raw_text", "") or ""
                            )[:80]
                            _diag = str(
                                getattr(self.cd_ocr, "_last_diag", "") or ""
                            )[:100]
                            _ready = bool(self.cd_ocr.ready())
                            self.log(
                                f"[ATK-CD-SNAP] ready={_ready} "
                                f"skills={_skills_snap} raw={_raw_snap!r} "
                                f"diag={_diag!r}"
                            )
                        except Exception:
                            pass
                        self._last_cd_diag_ts = now_emit
            except Exception:
                pass
            try:
                if fg_ok and self.xp_ocr.ready():
                    self.xp_ocr.submit_frame(frame, origin)
                # 사냥 분석 — xp 절대값 + 시간당 경험치 갱신.
                try:
                    cur_xp_abs = int(getattr(self.xp_ocr, "_last_xp", 0) or 0)
                    if cur_xp_abs > 0:
                        self.analytics.on_xp(cur_xp_abs)
                    xph = int(self.xp_ocr.xp_per_hour() or 0)
                    if xph > 0:
                        self.analytics.on_xph(xph)
                except Exception:
                    pass
            except Exception:
                pass

            # 사냥 분석 tick — idle 타임아웃 시 세션 자동 종료.
            try:
                closed = self.analytics.tick()
                if closed is not None:
                    self.log(
                        f"[HUNT-IDLE-CLOSE] id={closed.session_id} "
                        f"dur={closed.duration_sec}s xp={closed.xp_gain} "
                        f"laps={len(closed.laps)}"
                    )
            except Exception:
                pass

            # F1 에지 감지 (2026-06-12): down 0→1 전이 시 재고정 트리거 카운터++.
            # 힐러가 reanchor_seq 증가 감지 → 빨탭 재고정 시퀀스 즉시 실행.
            if _HAVE_WIN32:
                f1_down = bool(win32api.GetAsyncKeyState(_VK_F1) & 0x8000)
                if f1_down and not self._f1_prev_down:
                    self._reanchor_seq += 1
                    self.log(
                        f"[ATK-F1] 격수 빨탭 재고정 트리거 송신 "
                        f"seq={self._reanchor_seq}"
                    )
                self._f1_prev_down = f1_down
                # F2 에지 감지 (2026-06-12): 쩔캐(현인) 지폭지술 트리거.
                f2_down = bool(win32api.GetAsyncKeyState(_VK_F2) & 0x8000)
                if f2_down and not self._f2_prev_down:
                    self._jipok_seq += 1
                    self.log(
                        f"[ATK-F2] 쩔캐 지폭지술 트리거 송신 "
                        f"seq={self._jipok_seq}"
                    )
                self._f2_prev_down = f2_down
            _t_now = time.time()
            pending_now = (_t_now < self._map_chg_until)

            # HP/MP 즉시 읽기 (픽셀 비율, 가벼움). 포커스 없을 땐 skip.
            _hp_pct = -1
            _mp_pct = -1
            if fg_ok:
                try:
                    origin_xy = (
                        int(self.grab.mon.get("left", 0)),
                        int(self.grab.mon.get("top", 0)),
                    )
                    hr = self.hpmp.read(frame, origin_xy)
                    _hp_pct = int(getattr(hr, "hp", -1))
                    _mp_pct = int(getattr(hr, "mp", -1))
                except Exception:
                    pass

            st = State(
                seq=self._seq,
                ts_ms=now_ms(),
                map_name=self._last.map_name,
                coord_valid=self._last.coord_valid,
                x=self._last.x, y=self._last.y,
                last_dir=self._last.last_dir,
                map_seq=self._map_seq,
                map_change_pending=pending_now,
                reanchor_seq=self._reanchor_seq,
                jipok_seq=self._jipok_seq,
                hp_pct=_hp_pct,
                mp_pct=_mp_pct,
            )

            if frame_i % every == 0 and fg_ok:
                # 2026-04-22: AsyncOcr — submit 은 ≈0ms, latest 는 백그라운드
                # 최근 결과 (아직 없으면 None). 첫 프레임만 None 스킵.
                _t_ocr0 = time.perf_counter()
                self._ocr_async.submit(frame)
                r = self._ocr_async.latest()
                _stats_ocr_ms += (time.perf_counter() - _t_ocr0) * 1000.0
                _stats_ocr_n += 1
                # 백그라운드 predict 평균(참고용) — 루프 점유와 무관.
                self._stats_ocr_bg_ms_acc = getattr(
                    self, "_stats_ocr_bg_ms_acc", 0.0
                ) + float(self._ocr_async.last_predict_ms())
            if frame_i % every == 0 and fg_ok and r is not None:
                last_raw_c = r.raw_coord_text
                last_raw_m = r.raw_map_text
                last_ok = r.coord is not None
                now_ts = time.time()
                # 관측 맵 누적 → OCR 내부 canonical 교정 활성.
                # 짧은 noise 버전("선비족24")은 긴 정답("선비족2-4") 관측
                # 시 observed 에서 퇴출하여 이후 교정이 긴 쪽으로 수렴.
                if r.map_name:
                    nm = r.map_name
                    skip = False
                    to_drop = []
                    for km in self._observed_maps:
                        if km == nm:
                            continue
                        if _is_ocr_noise(km, nm):
                            if len(km) < len(nm):
                                to_drop.append(km)
                            else:
                                skip = True
                    for k in to_drop:
                        self._observed_maps.discard(k)
                    if not skip:
                        self._observed_maps.add(nm)
                    try:
                        self.ocr.set_known_maps(self._observed_maps)
                    except Exception:
                        pass
                # 2026-04-22: 같은 맵 이름 내 좌표 급변 → 워프 확정. map_seq++ 로
                # 힐러 MAP-SEQ-EDGE 경로 태워 anchor 리셋. 맵이름 변경 전 평가해서
                # 맵이름 변경 분기와 중복 방지 (coord_valid 반드시 True 체크).
                if (r.coord and r.map_name and r.map_name == self._prev_sent_map
                        and self._last.coord_valid):
                    _pwx, _pwy = self._last.x, self._last.y
                    _dj = abs(r.coord[0] - _pwx) + abs(r.coord[1] - _pwy)
                    if _dj > self._warp_threshold:
                        self._map_seq += 1
                        self._map_burst_remaining = self._map_burst_n
                        self.log(
                            f"[ATK-WARP] same_map={r.map_name!r} "
                            f"prev=({_pwx},{_pwy}) new={r.coord} d={_dj} "
                            f"thr={self._warp_threshold} "
                            f"→ map_seq={self._map_seq}"
                        )
                        # 좌표급변=맵전환 시작 (워프 거의 없음). 맵이름 OCR
                        # 갱신(ATK-MAP-EDGE)까지 map_change_pending 강제 ON.
                        self._map_chg_until = time.time() + 4.0
                # 2026-04-22 B안: 맵 이름 변경 감지 — map_seq++ + burst 만 수행.
                # 이전 coord suppress(hold) 로직은 제거. 맵 전환 순간 OCR "새맵+
                # 옛좌표" 오염 방지는 힐러측 _fresh_map_guard(exit_coord 근접 거부)
                # + 맵별 jump_reject 로 이미 커버됨. 격수가 0.5초 간 coord_valid
                # =False 로 쏘는 동안 힐러 UI 가 "-" 로 깜빡이는 증상 제거.
                if r.map_name and r.map_name != self._prev_sent_map:
                    if self._prev_sent_map:
                        self._map_seq += 1
                        self._map_burst_remaining = self._map_burst_n
                        self.log(
                            f"[ATK-MAP-EDGE] {self._prev_sent_map!r}→"
                            f"{r.map_name!r} map_seq={self._map_seq} "
                            f"burst={self._map_burst_n}"
                        )
                    self._prev_sent_map = r.map_name
                    self._map_chg_until = 0.0  # 맵이름 갱신 = 맵전환 완료
                    # 사냥 분석 — 바퀴 이벤트.
                    try:
                        lap = self.analytics.on_map(r.map_name)
                        if lap is not None:
                            self.log(
                                f"[HUNT-LAP] #{lap.lap_idx} map={lap.map_top} "
                                f"dur={lap.duration_sec}s gain={lap.xp_gain}"
                            )
                    except Exception:
                        pass
                # 2026-04-22 B안: hold 분기 제거. OCR 이 좌표를 뽑으면 그대로
                # coord_valid=True 송신. 힐러측 fresh_map_guard + jump_reject 로
                # trail 오염 방지.
                if r.coord:
                    st.coord_valid = True
                    st.x, st.y = r.coord
                    self._coord_hist.append(r.coord)
                    st.last_dir = self._dir()
                if r.map_name:
                    st.map_name = r.map_name
                if not r.coord:
                    try:
                        box = getattr(self.ocr, "_last_coord_box", None)
                        self.log(f"[ocr-fail] raw={r.raw_coord_text!r} "
                                 f"frame={frame.shape[1]}x{frame.shape[0]} "
                                 f"crop_box={box}")
                    except Exception:
                        pass

            # OCR 블록에서 map_seq가 증가했을 수 있으므로 송신 직전 재스탬프.
            st.map_seq = self._map_seq
            # 좌표급변(OCR블록에서 _map_chg_until 갱신됨) 반영 — OCR 후 재스탬프.
            st.map_change_pending = (time.time() < self._map_chg_until)
            # 2026-04-22: 격수 YOLO 빨탭 인식 (이전엔 "빨탭 없음 → YOLO 불필요"
            # 로 스킵했으나 스킬범위 오버레이가 자기 빨탭 좌표를 요구 → 활성화).
            # 힐러와 동일 학습 가중치(cfg.vision.weights) 공유.
            # detect_red: 최소 크기 + conf 최고 1개 빨탭. 실패 시 red_tab=False.
            # 2026-04-22: 격수 YOLO 도 힐러처럼 AsyncYolo 로 비동기화.
            # 동기 detect_red 는 WDDM GPU queue 경합 시 5ms→700ms 까지 튀어
            # 메인 루프 fps 8~12 로 급락 (격수.log 실측). submit/latest 패턴으로
            # 메인 루프 점유는 submit 의 ≈0ms 만 — target 60Hz 달성.
            try:
                if getattr(self, "_yolo", None) is None:
                    from ..vision.yolo import YoloRunner, AsyncYolo
                    try:
                        self._yolo = YoloRunner(
                            self.cfg.vision.weights,
                            imgsz=self.cfg.vision.imgsz,
                            conf=float(getattr(self.cfg.vision, "conf", 0.25)),
                            iou=self.cfg.vision.iou,
                            half=self.cfg.vision.half,
                            device=self.cfg.vision.device,
                            log_fn=self.log,
                        )
                        self._yolo_async = AsyncYolo(self._yolo)
                        self.log(
                            f"[attacker][YOLO] 빨탭 모델 로드(async) "
                            f"weights={self.cfg.vision.weights} "
                            f"imgsz={self.cfg.vision.imgsz} "
                            f"conf={float(getattr(self.cfg.vision, 'conf', 0.25))} "
                            f"iou={self.cfg.vision.iou} "
                            f"half={self.cfg.vision.half} "
                            f"device={self.cfg.vision.device}"
                        )
                    except Exception as _e:
                        self._yolo = False  # sentinel — 재시도 방지.
                        self._yolo_async = None
                        self.log(f"[attacker][YOLO] 로드 실패: {_e}")
                if self._yolo and getattr(self, "_yolo_async", None) is not None:
                    # 메인 루프 비블로킹 — 최신 frame 제출만.
                    _t_yolo0 = time.perf_counter()
                    self._yolo_async.submit(frame, (0, 0))
                    new_dets, _off, age_ms, predict_ms = self._yolo_async.latest()
                    _stats_yolo_ms += (time.perf_counter() - _t_yolo0) * 1000.0
                    _stats_yolo_n += 1
                    # 백그라운드 predict 실측은 별도 로깅 (루프 점유와 구분).
                    _stats_yolo_bg_ms = getattr(
                        self, "_stats_yolo_bg_ms_acc", 0.0
                    ) + float(predict_ms)
                    self._stats_yolo_bg_ms_acc = _stats_yolo_bg_ms
                    # age_ms>=0 이면 최소 1회 detection 완료 — RED 중 conf 최고 1개 선택.
                    det = None
                    if age_ms >= 0:
                        for d in new_dets:
                            if d.w < 25 or d.h < 40:
                                continue
                            if d.tab_color != "RED":
                                continue
                            if det is None or d.conf > det.conf:
                                det = d
                    now_tab = time.time()
                    if det is not None:
                        mon = self.grab.mon
                        # 박스 중심은 기본 좌표로 유지(legacy 폴백). 실제
                        # 기준점은 스킬범위 오버레이가 방향 + bbox + 캐릭터
                        # 반크기 로 자체 계산 — 무기 확장 문제 회피.
                        st.red_tab = True
                        st.red_cx = int(mon["left"]) + int(det.cx)
                        st.red_cy = int(mon["top"]) + int(det.cy)
                        # YOLO bbox 절대 좌표 전달용.
                        self._last_red_box = (
                            int(mon["left"]) + int(det.x1),
                            int(mon["top"]) + int(det.y1),
                            int(mon["left"]) + int(det.x2),
                            int(mon["top"]) + int(det.y2),
                        )
                        self._last_red_cx = st.red_cx
                        self._last_red_cy = st.red_cy
                        self._last_red_ts = now_tab
                    else:
                        # 마법 이펙트/UI 로 빨탭이 가려지는 짧은 구간엔 직전
                        # 좌표 sticky 유지 — 오버레이 깜빡임 방지.
                        # TTL 초과 시에만 red_tab=False 로 전환.
                        ts = getattr(self, "_last_red_ts", 0.0)
                        ttl = getattr(self, "_red_ttl_sec", 3.0)
                        if ts > 0 and (now_tab - ts) <= ttl:
                            st.red_tab = True
                            st.red_cx = int(getattr(
                                self, "_last_red_cx", 0
                            ))
                            st.red_cy = int(getattr(
                                self, "_last_red_cy", 0
                            ))
                        else:
                            st.red_tab = False
            except Exception as _e:
                try:
                    self.log(f"[attacker][YOLO] 추론 err: {_e}")
                except Exception:
                    pass
            pkt = st.to_bytes()
            # 2026-04-23: 송신 스로틀 — 루프는 무제한이지만 UDP 는 send_rate_hz 고정.
            # 맵전환 burst 는 스로틀 무시하고 즉시 송신 (loss 대비).
            _t_send_now = time.perf_counter()
            if _t_send_now - _last_send_t >= send_period:
                self.sender.send(pkt)
                _last_send_t = _t_send_now
                _send_count += 1
            if self._map_burst_remaining > 0:
                # 연속 2~3회 추가 전송 (현재 프레임에서 즉시, 스로틀 무시).
                for _ in range(2):
                    self.sender.send(pkt)
                self._map_burst_remaining -= 1
                if self._map_burst_remaining == 0:
                    self.log(f"[ATK-MAP-BURST-END] map_seq={self._map_seq}")
            self._last = st
            self._seq += 1
            frame_i += 1

            now = time.time()
            if self.stat_cb:
                try:
                    self.stat_cb({
                        "seq": st.seq, "map": st.map_name,
                        "coord": (st.x, st.y), "valid": st.coord_valid,
                        "dir": st.last_dir,
                        # 2026-04-22: 격수 스킬 범위 오버레이용 원천 데이터.
                        "red_tab": bool(st.red_tab),
                        "red_cx": int(st.red_cx),
                        "red_cy": int(st.red_cy),
                        "red_box": tuple(self._last_red_box),
                        "peers": self.cfg.net.peers,
                        "port": self.cfg.net.port,
                        # 2026-04-22: UI 라벨(lbl_self_map/lbl_self_coord,
                        # status_strip, 기존 lbl_map/lbl_acoord) 은 atk_* 키를
                        # 본다. healer 측 payload 와 키 통일.
                        "atk_map": st.map_name,
                        "atk_coord": (
                            (int(st.x), int(st.y)) if st.coord_valid else None
                        ),
                    })
                except Exception:
                    pass
            # 2026-04-22: loop 한 바퀴 실측 (sleep 제외). drop/평균 산출.
            _stats_loop_ms += (time.perf_counter() - _t_loop0) * 1000.0
            _stats_frames += 1

            if now - last_log >= 1.0:
                _fps = _stats_frames / max(1e-6, (now - last_log)) if last_log > 0 else _stats_frames
                _loop_avg = _stats_loop_ms / max(1, _stats_frames)
                _yolo_avg = _stats_yolo_ms / max(1, _stats_yolo_n)
                _ocr_avg = _stats_ocr_ms / max(1, _stats_ocr_n)
                _over_avg = _stats_over_ms / max(1, _stats_drops) if _stats_drops else 0.0
                self.log(f"[attacker] seq={st.seq} map={st.map_name[:10]!r} "
                         f"coord=({st.x},{st.y}) valid={int(st.coord_valid)} "
                         f"dir={st.last_dir} "
                         f"red_tab={int(st.red_tab)} "
                         f"red_cx={st.red_cx} red_cy={st.red_cy} "
                         f"raw={last_raw_c!r} raw_m={last_raw_m!r} "
                         f"ocr_ok={int(last_ok)}")
                # yolo_submit_avg = 메인루프 submit+latest 호출 시간 (≈0).
                # yolo_bg_avg = 백그라운드 스레드 실제 predict 평균 (참고값,
                # 루프 점유와 무관). 이 값 >> period 여도 메인 루프는 안 막힘.
                _yolo_bg_acc = getattr(self, "_stats_yolo_bg_ms_acc", 0.0)
                _yolo_bg_avg = _yolo_bg_acc / max(1, _stats_yolo_n)
                _ocr_bg_acc = getattr(self, "_stats_ocr_bg_ms_acc", 0.0)
                _ocr_bg_avg = _ocr_bg_acc / max(1, _stats_ocr_n)
                _send_fps = _send_count / max(1e-6, (now - last_log)) if last_log > 0 else _send_count
                self.log(f"[attacker][PERF] loop_fps={_fps:.1f} "
                         f"send_fps={_send_fps:.1f} "
                         f"send_target={self.cfg.net.send_rate_hz}Hz "
                         f"loop_avg={_loop_avg:.1f}ms "
                         f"yolo_submit_avg={_yolo_avg:.2f}ms(n={_stats_yolo_n}) "
                         f"yolo_bg_avg={_yolo_bg_avg:.1f}ms "
                         f"ocr_submit_avg={_ocr_avg:.2f}ms(n={_stats_ocr_n}) "
                         f"ocr_bg_avg={_ocr_bg_avg:.1f}ms "
                         f"send_period={send_period*1000:.1f}ms")
                last_log = now
                _stats_frames = 0
                _send_count = 0
                _stats_drops = 0
                _stats_over_ms = 0.0
                _stats_loop_ms = 0.0
                _stats_yolo_ms = 0.0
                _stats_yolo_n = 0
                _stats_ocr_ms = 0.0
                _stats_ocr_n = 0
                self._stats_yolo_bg_ms_acc = 0.0
                self._stats_ocr_bg_ms_acc = 0.0

            # 2026-04-23: 루프 pacing 제거 — 캡처/YOLO/OCR 은 하드웨어 한계까지.
            # UDP 송신은 위쪽 send_period 게이트로 스로틀됨.

        self.sender.close()
        # timeBeginPeriod 호출했으면 반드시 timeEndPeriod 로 복원.
        if _timer_period_set:
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
        self.log("[attacker] 종료")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    cfg = load_cfg() if args.config is None else load_cfg(args.config)
    app = Attacker(cfg)
    signal.signal(signal.SIGINT, app.stop)
    app.run()


if __name__ == "__main__":
    main()

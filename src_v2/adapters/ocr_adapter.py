"""OCR adapter — wraps src/vision/ocr.py + map_ocr.py + cooldown/buff/chat OCR.

v1 SoR (2026-04-25):
  - src/vision/ocr.py: Ocr / AsyncOcr (coord + map_name).
    set_known_maps(names) — UDP 수신 맵 이름 집합 주입.
  - src/vision/cooldown_ocr.py: CooldownOcr (쿨/버프/채팅 OCR 공용).
    set_region(x,y,w,h) / set_target_skills([...]) / submit_frame(frame, origin).

v2 어댑터는 thin wrapper — v1 객체 메서드를 호출만 하고, getattr 로 누락 시 no-op.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger("src_v2.adapters.ocr")


class SrcOcrAdapter:
    """Wraps coord OCR + optional map OCR + cooldown/buff/chat OCRs.

    추가 wiring (v2 1:1):
      cd_ocr / buff_ocr / chat_ocr 인스턴스 등록 가능.
      set_region("cd"|"buff"|"chat"|"nick", x,y,w,h)
      set_target_skills("cd"|"buff", names)
      set_known_maps(names) — Ocr.set_known_maps 위임.
      submit_cd_frame(frame, origin) / submit_buff_frame / submit_chat_frame.
      latest_cd() / latest_buff() / latest_chat() — v1 CooldownReading 그대로 반환.
    """

    def __init__(self, coord_ocr: Any, map_ocr: Any = None,
                 cd_ocr: Any = None, buff_ocr: Any = None,
                 chat_ocr: Any = None) -> None:
        self._coord = coord_ocr
        self._map = map_ocr
        # v1 CooldownOcr 인스턴스들 (옵션). 어댑터 사용자가 워커 측에서 주입.
        self._cd = cd_ocr
        self._buff = buff_ocr
        self._chat = chat_ocr

    def read(self, frame: Any) -> Tuple[Optional[Tuple[int, int]], str]:
        if frame is None:
            return (None, "")
        coord: Optional[Tuple[int, int]] = None
        map_name = ""
        try:
            if self._coord is not None:
                for m in ("read_coord", "read", "infer"):
                    fn = getattr(self._coord, m, None)
                    if callable(fn):
                        r = fn(frame)
                        if isinstance(r, tuple) and len(r) >= 2:
                            x, y = r[0], r[1]
                            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                                coord = (int(x), int(y))
                        elif r and hasattr(r, "coord"):
                            coord = r.coord
                        break
        except Exception:  # noqa: BLE001
            log.exception("coord ocr fail")
        try:
            if self._map is not None:
                for m in ("read_map", "read", "infer", "name"):
                    fn = getattr(self._map, m, None)
                    if callable(fn):
                        r = fn(frame)
                        if isinstance(r, str):
                            map_name = r
                        elif r and hasattr(r, "map_name"):
                            map_name = r.map_name or ""
                        break
        except Exception:  # noqa: BLE001
            log.exception("map ocr fail")
        return (coord, map_name)

    def is_available(self) -> bool:
        return self._coord is not None or self._map is not None

    # ------------------------------------------------------------------
    # v1 wiring helpers — 외부(adapter setup 코드)가 인스턴스 주입 후 호출.
    # ------------------------------------------------------------------
    def attach_cd(self, cd_ocr: Any) -> None: self._cd = cd_ocr
    def attach_buff(self, buff_ocr: Any) -> None: self._buff = buff_ocr
    def attach_chat(self, chat_ocr: Any) -> None: self._chat = chat_ocr

    def set_known_maps(self, names) -> None:
        """v1 Ocr.set_known_maps 위임. coord/map 어느 쪽이든 지원."""
        for tgt in (self._coord, self._map):
            try:
                fn = getattr(tgt, "set_known_maps", None)
                if callable(fn):
                    fn(names)
            except Exception:  # noqa: BLE001
                log.exception("set_known_maps fail")

    def set_region(self, kind: str, x: int, y: int, w: int, h: int) -> None:
        """kind: "cd" | "buff" | "chat" | "nick".

        v1 CooldownOcr.set_region(x,y,w,h) 1:1.
        nick 은 set_nick_region 호출.
        """
        target = self._resolve_kind(kind)
        if target is None:
            return
        try:
            if kind == "nick":
                fn = getattr(target, "set_nick_region", None)
            else:
                fn = getattr(target, "set_region", None)
            if callable(fn):
                fn(int(x), int(y), int(w), int(h))
        except Exception:  # noqa: BLE001
            log.exception("set_region(%s) fail", kind)

    def clear_region(self, kind: str) -> None:
        target = self._resolve_kind(kind)
        if target is None:
            return
        try:
            if kind == "nick":
                fn = getattr(target, "clear_nick_region", None)
            else:
                fn = getattr(target, "clear_region", None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001
            log.exception("clear_region(%s) fail", kind)

    def set_target_skills(
        self,
        kind: str,
        names_or_mapping: Union[List[str], Dict[str, List[str]]],
    ) -> None:
        """kind: "cd" | "buff" — v1 CooldownOcr.set_target_skills 1:1."""
        target = self._resolve_kind(kind)
        if target is None:
            return
        try:
            fn = getattr(target, "set_target_skills", None)
            if callable(fn):
                fn(names_or_mapping)
        except Exception:  # noqa: BLE001
            log.exception("set_target_skills(%s) fail", kind)

    def submit_frame(self, kind: str, frame, origin: Tuple[int, int] = (0, 0)) -> None:
        """v1 CooldownOcr.submit_frame 1:1. 메인 루프가 매 프레임 비블로킹 호출.

        kind: "cd" | "buff" | "chat".
        """
        target = self._resolve_kind(kind)
        if target is None:
            return
        try:
            fn = getattr(target, "submit_frame", None)
            if callable(fn):
                fn(frame, tuple(origin))
        except Exception:  # noqa: BLE001
            log.exception("submit_frame(%s) fail", kind)

    def latest(self, kind: str) -> Any:
        """v1 CooldownOcr.latest() 결과 그대로 반환. 호출자가 구조 파싱."""
        target = self._resolve_kind(kind)
        if target is None:
            return None
        try:
            fn = getattr(target, "latest", None)
            if callable(fn):
                return fn()
        except Exception:  # noqa: BLE001
            log.exception("latest(%s) fail", kind)
        return None

    def start_aux(self) -> None:
        """cd/buff/chat 백그라운드 스레드 일괄 start."""
        for tgt in (self._cd, self._buff, self._chat):
            try:
                fn = getattr(tgt, "start", None)
                if callable(fn):
                    fn()
            except Exception:  # noqa: BLE001
                log.exception("aux start fail")

    def stop_aux(self) -> None:
        for tgt in (self._cd, self._buff, self._chat):
            try:
                fn = getattr(tgt, "stop", None)
                if callable(fn):
                    fn()
            except Exception:  # noqa: BLE001
                pass

    def _resolve_kind(self, kind: str) -> Any:
        k = (kind or "").lower()
        if k == "cd":
            return self._cd
        if k == "buff":
            return self._buff
        if k == "chat":
            return self._chat
        if k == "nick":
            # nick 은 cd_ocr 인스턴스에 부속 (v1 CooldownOcr.set_nick_region).
            return self._cd
        return None

    def stop(self) -> None:
        for o in (self._coord, self._map):
            try:
                fn = getattr(o, "stop", None)
                if callable(fn):
                    fn()
            except Exception:  # noqa: BLE001
                pass
        self.stop_aux()


class RealOcrAdapter(SrcOcrAdapter):
    """Production adapter — wraps src.vision.ocr.Ocr (+AsyncOcr) for coord+map.

    Frame -> Ocr.read() returns (coord, map_name, raw_*) — we use the async
    wrapper for consistency with attacker.py / healer_worker.py.
    """

    def __init__(self, ocr_cfg: Any, gpu: bool = True) -> None:
        from src.vision.ocr import Ocr, AsyncOcr  # lazy
        ocr = Ocr(
            coord_w=ocr_cfg.coord_w,
            coord_h=ocr_cfg.coord_h,
            coord_right_pad=ocr_cfg.coord_right_pad,
            coord_bottom_pad=ocr_cfg.coord_bottom_pad,
            coord_upscale=ocr_cfg.coord_upscale,
            map_w=ocr_cfg.map_w,
            map_h=ocr_cfg.map_h,
            map_top_pad=ocr_cfg.map_top_pad,
            map_left_pad=getattr(ocr_cfg, "map_left_pad", -1),
            map_upscale=ocr_cfg.map_upscale,
            gpu=gpu,
        )
        self._async = AsyncOcr(ocr)
        # both coord & map go through the same Ocr instance
        super().__init__(coord_ocr=self._async, map_ocr=self._async)
        self._ocr_raw = ocr
        # 진단: read 호출 카운터 + 첫 latest 등장 ts. ocr_watcher 가 stats 로 노출 가능.
        self._read_call_count: int = 0
        self._latest_first_ts: float = 0.0
        # 2026-05-05 Cycle 4-10 — coord/map picker 역산용 game_region 보유.
        # set_game_region 호출 시 저장 → set_coord_region/set_map_region 가
        # 이 game_region 기준으로 OcrCfg 패딩 역산.
        self._game_region: Optional[tuple] = None

    # ---- 2026-05-05 Cycle 4-10: picker → 패딩 역산 setter 3종 ---- #
    def set_game_region(self, x: int, y: int, w: int, h: int) -> None:
        """game_region 절대 좌표 보유 (coord/map picker 역산용)."""
        self._game_region = (int(x), int(y), int(w), int(h))

    def set_coord_region(self, x: int, y: int, w: int, h: int) -> None:
        """좌표 영역 picker → Ocr 의 coord_w/coord_h/coord_right_pad/coord_bottom_pad
        attribute 직접 갱신 (runtime).

        역산 공식 (game_region = (gx, gy, gw, gh)):
            coord_w = w
            coord_h = h
            coord_right_pad = (gx + gw) - (x + w)   # game 우측 끝부터 안쪽 거리
            coord_bottom_pad = (gy + gh) - (y + h)  # game 아래 끝부터 안쪽 거리
        """
        gr = self._game_region
        if gr is None:
            log.warning(
                "[OCR] set_coord_region called before set_game_region — "
                "game_region 알 수 없어 패딩 역산 불가"
            )
            return
        gx, gy, gw, gh = gr
        try:
            ocr = self._ocr_raw
            ocr.coord_w = int(w)
            ocr.coord_h = int(h)
            ocr.coord_right_pad = int((gx + gw) - (int(x) + int(w)))
            ocr.coord_bottom_pad = int((gy + gh) - (int(y) + int(h)))
            log.info(
                "[OCR] coord_region picker → w=%d h=%d right_pad=%d bottom_pad=%d",
                ocr.coord_w, ocr.coord_h,
                ocr.coord_right_pad, ocr.coord_bottom_pad,
            )
        except Exception:  # noqa: BLE001
            log.exception("set_coord_region 패딩 갱신 fail")

    def set_map_region(self, x: int, y: int, w: int, h: int) -> None:
        """맵이름 영역 picker → Ocr 의 map_w/map_h/map_top_pad/map_left_pad 갱신.

        역산:
            map_w = w
            map_h = h
            map_top_pad = y - gy           # game 위 끝에서 map 까지 거리
            map_left_pad = x - gx          # game 좌 끝에서 map 까지 거리 (-1=중앙 default 무시)
        """
        gr = self._game_region
        if gr is None:
            log.warning(
                "[OCR] set_map_region called before set_game_region — "
                "역산 불가"
            )
            return
        gx, gy, gw, gh = gr
        try:
            ocr = self._ocr_raw
            ocr.map_w = int(w)
            ocr.map_h = int(h)
            ocr.map_top_pad = int(int(y) - gy)
            ocr.map_left_pad = int(int(x) - gx)
            log.info(
                "[OCR] map_region picker → w=%d h=%d top_pad=%d left_pad=%d",
                ocr.map_w, ocr.map_h,
                ocr.map_top_pad, ocr.map_left_pad,
            )
        except Exception:  # noqa: BLE001
            log.exception("set_map_region 패딩 갱신 fail")

    def read(self, frame):
        if frame is None:
            return (None, "")
        try:
            self._read_call_count += 1
            self._async.submit(frame)
            r = self._async.latest()
            if r is None:
                # 처음 N회는 워커 가 아직 첫 read 못한 정상 케이스 — None 반환.
                return (None, "")
            if self._latest_first_ts == 0.0:
                import time as _t
                self._latest_first_ts = _t.time()
            coord = r.coord if r.coord else None
            map_name = r.map_name or ""
            return (coord, map_name)
        except Exception:  # noqa: BLE001
            log.exception("real ocr read fail")
            return (None, "")

    def diag(self) -> dict:
        """진단 dict — ocr_watcher 가 첫 tick 후 호출."""
        try:
            return {
                "read_call_count": int(self._read_call_count),
                "latest_first_ts": float(self._latest_first_ts),
                "async_last_predict_ms": float(self._async.last_predict_ms()),
                "async_thread_alive": bool(self._async._thread.is_alive()),
                "easy_device": getattr(self._ocr_raw, "_easy_device_note", "?"),
            }
        except Exception:
            return {}

    def stop(self) -> None:
        try:
            self._async.stop()
        except Exception:  # noqa: BLE001
            pass

    @property
    def raw_ocr(self):
        """Underlying Ocr instance — for set_known_maps() etc."""
        return self._ocr_raw

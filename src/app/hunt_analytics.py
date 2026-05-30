r"""사냥 세션/바퀴 분석.

데이터 수집:
  - 맵 변경 이벤트: LapTracker
      * `..-M(1)` 진입 → lap_start (현재 바퀴 타이머 시작, 시작 xp 스냅샷).
      * 상위맵 (괄호 없음) 복귀 → lap_end (duration, xp_gain 기록).
      * 중간에 (2)~(7) 이동은 바퀴 내부이므로 무시.
      * 상위맵 식별: `^(.+?)-\d+\(\d+\)?$` 의 \1 그룹. OCR이 `)` 자주 누락해
        `\)?` 로 관대하게 매칭.
  - XP 샘플: HuntSession
      * xp 최초 증가 → 세션 활성화.
      * xp 감소(레벨업 추정) → _total_delta 에 이전 peak - start_xp 누산.
      * 60s 동안 xp 변동 없으면 세션 종료 → 저장.
      * 워커 stop 시 강제 종료.

영속:
  `hunt_reports/YYYY-MM-DD.jsonl` 에 세션 1건당 1줄 append.
  폴더 없으면 자동 생성. 세션 id=start_ts(timezone-aware epoch ms).

읽기:
  HuntReportStorage.list_dates() / read_date(d) — 리포트 다이얼로그가 호출.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


# 맵 이름 패턴 — `.*-숫자(숫자)?` 형태.
# OCR이 닫는 괄호 자주 누락 → `\)?` 관대.
_MAP_DUNGEON_RE = re.compile(r"^(.+?)-(\d+)\((\d+)\)?$")

# lap 괄호 스트립용 — `선비족2-2(1)` `선비족2-2(1` 등 → `선비족2-2`.
_LAP_SUFFIX_RE = re.compile(r"\(\d+\)?\s*$")


def parse_map_name(name: str) -> Tuple[Optional[str], Optional[int]]:
    """맵 이름 → (상위맵 이름, 굴 번호 K). 상위맵이면 (name, None)."""
    if not name:
        return (None, None)
    m = _MAP_DUNGEON_RE.match(name.strip())
    if m:
        return (m.group(1), int(m.group(3)))
    return (name.strip(), None)


def strip_lap_suffix(name: str) -> str:
    """`선비족2-2(1)` → `선비족2-2`. 괄호가 없으면 그대로."""
    if not name:
        return ""
    return _LAP_SUFFIX_RE.sub("", str(name).strip()).strip()


@dataclass
class LapRecord:
    start_ts: float          # epoch seconds (wall clock)
    duration_sec: int        # 양 끝단 시간차.
    xp_gain: int             # 바퀴 내 경험치 증가분 (레벨업 누적 포함).
    map_top: str             # 상위맵 이름.
    lap_idx: int             # 세션 내 바퀴 순번 (1부터).


@dataclass
class SessionRecord:
    session_id: str          # YYYYMMDD_HHMMSS
    start_ts: float
    end_ts: float
    duration_sec: int
    xp_gain: int
    laps: List[Dict] = field(default_factory=list)
    peak_xp_per_hour: int = 0
    map_top: str = ""        # 주 사냥 맵 (가장 많이 나온 상위맵).
    # 세션 내 방문 순서(lap 괄호 제거 후 dedup, 예: 선비족2-2 / 선비족2-3 …).
    # 리포트 상세에서 "어디 돌았는지" 조회용.
    map_history: List[str] = field(default_factory=list)
    # 베이스 맵별 체류 누적 (count = on_map 호출 건수). 정렬된 리스트 of (name, count).
    map_counts: List[List] = field(default_factory=list)
    # 베이스 맵별 상세 집계 — note_map_raw 시점 간 시간/xp 증가 diff 누적.
    # [{"name": str, "count": int, "duration_sec": int, "xp_gain": int}, ...]
    # 방문 순서(첫 등장 기준). 리포트 상세의 "사냥터 | 시간 | 경험치" 표시용.
    map_stats: List[Dict] = field(default_factory=list)


class LapTracker:
    """맵 이벤트 기반 바퀴 타이머.

    스레드-세이프 lock — attacker 메인 루프(메인 스레드) + xp_ocr 백그라운드(읽기)
    가 동시에 접근.
    """

    def __init__(self, session: "HuntSession", max_keep: int = 200):
        self._lock = threading.RLock()
        self._session = session
        self._laps: Deque[LapRecord] = deque(maxlen=max_keep)
        self._in_dungeon = False
        self._cur_top: str = ""
        self._cur_lap_start_ts: float = 0.0
        self._cur_lap_start_xp: int = 0
        self._prev_map_name: str = ""
        self._lap_counter = 0  # 세션 내 순번.
        # 세션 리셋 신호.
        self._session_id_tag: str = ""

    def reset_for_session(self, session_id: str) -> None:
        with self._lock:
            self._laps.clear()
            self._in_dungeon = False
            self._cur_top = ""
            self._cur_lap_start_ts = 0.0
            self._cur_lap_start_xp = 0
            self._lap_counter = 0
            self._session_id_tag = session_id

    def on_map_change(self, new_map: str, cur_xp: int) -> Optional[LapRecord]:
        """맵 변경 감지 시 호출. 바퀴 이벤트 발생하면 LapRecord 반환, 아니면 None.
        cur_xp 는 격수 xp_ocr 의 마지막 절대값 (없으면 0).
        """
        now = time.time()
        rec: Optional[LapRecord] = None
        with self._lock:
            top, k = parse_map_name(new_map)
            if not top:
                self._prev_map_name = new_map
                return None
            # 세션이 아직 없으면 기록 시작 지연 (lap은 세션 활성화 후만 의미).
            session_active = self._session.active

            if k == 1 and not self._in_dungeon:
                # 바퀴 시작.
                self._in_dungeon = True
                self._cur_top = top
                self._cur_lap_start_ts = now
                self._cur_lap_start_xp = int(cur_xp or 0)
            elif k is None and self._in_dungeon:
                # 상위맵 복귀 — 바퀴 종료. 단, top 일치할 때만 (다른 맵 이탈 배제).
                if top == self._cur_top and self._cur_lap_start_ts > 0:
                    dur = max(0, int(now - self._cur_lap_start_ts))
                    gain = max(0, int(cur_xp) - self._cur_lap_start_xp) if cur_xp else 0
                    # 세션 레벨업 누적 보정 — 세션 총 xp_gain 과 바퀴 xp_gain 가
                    # 어긋날 수 있으나 바퀴는 직접 diff만 씀 (레벨업은 평균 왜곡 가능 리스크
                    # 수용: 대부분 한 바퀴 내 레벨업 드뭄).
                    self._lap_counter += 1
                    rec = LapRecord(
                        start_ts=self._cur_lap_start_ts,
                        duration_sec=dur,
                        xp_gain=gain,
                        map_top=self._cur_top,
                        lap_idx=self._lap_counter,
                    )
                    self._laps.append(rec)
                self._in_dungeon = False
                self._cur_lap_start_ts = 0.0
                self._cur_lap_start_xp = 0
            # k in 2..N 이거나 상위맵 다른 곳으로 이탈 — 무시 (바퀴 유지).

            self._prev_map_name = new_map
            return rec if session_active and rec else rec
        # 참고: 바퀴는 세션 활성 여부와 관계없이 누적 (세션 종료 시점에 flush).

    def snapshot(self) -> Dict:
        with self._lock:
            laps = list(self._laps)
            n = len(laps)
            if n > 0:
                avg_dur = int(sum(l.duration_sec for l in laps) / n)
                avg_gain = int(sum(l.xp_gain for l in laps) / n)
            else:
                avg_dur = 0
                avg_gain = 0
            cur_elapsed = 0
            if self._in_dungeon and self._cur_lap_start_ts > 0:
                cur_elapsed = int(time.time() - self._cur_lap_start_ts)
            return {
                "count": n,
                "avg_duration_sec": avg_dur,
                "avg_xp_gain": avg_gain,
                "in_progress": self._in_dungeon,
                "cur_elapsed_sec": cur_elapsed,
                "cur_top": self._cur_top,
                "last_laps": [asdict(l) for l in list(laps)[-5:]],
            }

    def drain_laps(self) -> List[LapRecord]:
        """세션 종료 시 지금까지 쌓인 바퀴 전체 반환 + 내부 비움."""
        with self._lock:
            laps = list(self._laps)
            self._laps.clear()
            self._in_dungeon = False
            self._cur_lap_start_ts = 0.0
            self._cur_lap_start_xp = 0
            return laps


class HuntSession:
    """경험치 변동 기반 사냥 세션.

    규칙:
      * xp 1 이상 증가 한 번 발생 → active=True, start_ts/ start_xp 스냅샷.
      * xp 감소 → 레벨업 간주, _total_delta 에 (last_peak_xp - start_xp) 누산 후
        start_xp = 새 xp.
      * idle_timeout_sec 동안 xp 변동 없음 → close (파일 저장).
      * force_close() — 워커 정지 시.
    """

    def __init__(self, storage: "HuntReportStorage",
                 lap_tracker: Optional[LapTracker] = None,
                 idle_timeout_sec: float = 60.0):
        self._lock = threading.RLock()
        self._storage = storage
        self.lap_tracker = lap_tracker
        self.idle_timeout_sec = float(idle_timeout_sec)
        self.active = False
        self.start_ts: float = 0.0
        self.session_id: str = ""
        self._start_xp: int = 0
        self._last_xp: int = 0
        self._last_change_ts: float = 0.0
        self._total_delta: int = 0
        self._peak_xph: int = 0
        self._map_count: Dict[str, int] = {}
        self._last_cur_xp: int = 0
        self._last_report: Optional[SessionRecord] = None
        # 방문한 원본 맵 이름(선비족X-X 포함) 순서대로 유지. 연속 중복은 합침.
        # 오버레이에 최근 N개 표시용.
        self._map_history: List[str] = []
        # 맵별 체류 상세 — 전이 이벤트 간 diff 로 누적.
        # _map_stats_cur: 현재 맵 {name, enter_ts, enter_xp} / 전이 시점에 정산.
        # _map_stats_agg: {name: {name, count, duration_sec, xp_gain}} 누적.
        self._map_stats_cur: Optional[Dict] = None
        self._map_stats_agg: Dict[str, Dict] = {}

    def _now(self) -> float:
        return time.time()

    def note_map(self, map_top: str) -> None:
        with self._lock:
            if not map_top:
                return
            self._map_count[map_top] = self._map_count.get(map_top, 0) + 1

    def note_map_raw(self, map_name: str) -> None:
        """오버레이용 방문 히스토리. `(X)` lap 괄호는 제거.

        `선비족2-2(1)` `선비족2-2(2)` 등은 모두 `선비족2-2`로 기록. 젠타임 관측이
        목적 — 같은 베이스 맵 내 여러 lap은 한 항목으로 합쳐야 겹치는지 보임.
        연속 중복은 합치고 끝에 추가.
        """
        with self._lock:
            if not map_name:
                return
            base = strip_lap_suffix(map_name)
            if not base:
                return
            if self._map_history and self._map_history[-1] == base:
                return
            # 이전 맵 체류 정산 — enter_ts/enter_xp 대비 now/last_xp diff.
            now = self._now()
            cur_xp = int(self._last_cur_xp)
            if self._map_stats_cur is not None:
                prev = self._map_stats_cur
                dur = max(0, int(now - float(prev.get("enter_ts") or now)))
                ex = int(prev.get("enter_xp") or 0)
                gain = max(0, cur_xp - ex) if cur_xp and ex else 0
                key = str(prev.get("name") or "")
                if key:
                    agg = self._map_stats_agg.get(key)
                    if agg is None:
                        agg = {"name": key, "count": 0,
                               "duration_sec": 0, "xp_gain": 0}
                        self._map_stats_agg[key] = agg
                    agg["count"] = int(agg["count"]) + 1
                    agg["duration_sec"] = int(agg["duration_sec"]) + dur
                    agg["xp_gain"] = int(agg["xp_gain"]) + gain
            self._map_history.append(base)
            self._map_stats_cur = {
                "name": base, "enter_ts": now, "enter_xp": cur_xp,
            }
            # 메모리 상한: 세션 내 최대 200개까지 유지 (오버레이는 마지막 5개만 사용).
            if len(self._map_history) > 200:
                self._map_history = self._map_history[-200:]

    def note_xph(self, xph: int) -> None:
        with self._lock:
            if xph and xph > self._peak_xph:
                self._peak_xph = int(xph)

    def on_xp(self, xp: int) -> None:
        """격수 xp_ocr 이 새 xp 값을 전달할 때 호출.

        정책: 최초 xp는 기준점만 잡고 active=False 유지. xp가 실제로 **증가**하는
        순간을 '사냥 시작'으로 판정 → 그때 active=True, start_ts/start_xp 스냅샷.
        켜자마자 사냥 분석이 올라가는 오동작 방지.
        """
        if not xp or xp <= 0:
            return
        with self._lock:
            self._last_cur_xp = int(xp)
            if not self.active:
                # 최초 유효 xp — 기준점만 잡고 비활성 유지.
                if self._last_xp <= 0:
                    self._last_xp = int(xp)
                    self._last_change_ts = self._now()
                    return
                if int(xp) > self._last_xp:
                    # 실제 증가 감지 → 사냥 시작.
                    self.active = True
                    self.start_ts = self._now()
                    self.session_id = _dt.datetime.fromtimestamp(
                        self.start_ts
                    ).strftime("%Y%m%d_%H%M%S")
                    self._start_xp = int(self._last_xp)
                    self._total_delta = 0
                    self._map_count.clear()
                    self._map_history.clear()
                    self._map_stats_agg.clear()
                    self._map_stats_cur = None
                    self._peak_xph = 0
                    if self.lap_tracker is not None:
                        self.lap_tracker.reset_for_session(self.session_id)
                    self._last_xp = int(xp)
                    self._last_change_ts = self._now()
                else:
                    # 같거나 감소 — 비활성 유지하며 기준값만 갱신.
                    self._last_xp = int(xp)
                return
            # active
            if int(xp) == self._last_xp:
                return
            if int(xp) < self._last_xp:
                # 레벨업: 직전까지 누적분 흡수.
                self._total_delta += max(0, self._last_xp - self._start_xp)
                self._start_xp = int(xp)
            self._last_xp = int(xp)
            self._last_change_ts = self._now()

    def cur_xp_gain(self) -> int:
        with self._lock:
            if not self.active:
                return 0
            return int(self._total_delta + max(0, self._last_xp - self._start_xp))

    def cur_duration(self) -> int:
        with self._lock:
            if not self.active:
                return 0
            return int(self._now() - self.start_ts)

    def cur_xp_per_hour(self) -> int:
        with self._lock:
            d = self.cur_duration()
            if d < 10:
                return 0
            return int(self.cur_xp_gain() / d * 3600.0)

    def tick(self) -> Optional[SessionRecord]:
        """매 루프 호출. idle 타임아웃 초과 시 close 하고 SessionRecord 반환."""
        with self._lock:
            if not self.active:
                return None
            if (self._now() - self._last_change_ts) >= self.idle_timeout_sec:
                return self._close_locked(reason="idle")
            return None

    def force_close(self) -> Optional[SessionRecord]:
        with self._lock:
            if not self.active:
                return None
            return self._close_locked(reason="stop")

    def _close_locked(self, reason: str) -> Optional[SessionRecord]:
        end_ts = self._now()
        dur = max(0, int(end_ts - self.start_ts))
        gain = int(self._total_delta + max(0, self._last_xp - self._start_xp))
        # 최빈 상위맵.
        top = ""
        if self._map_count:
            top = max(self._map_count.items(), key=lambda kv: kv[1])[0]
        laps_rec: List[Dict] = []
        if self.lap_tracker is not None:
            laps_rec = [asdict(l) for l in self.lap_tracker.drain_laps()]
        # 방문 히스토리 스냅샷 — 순서 유지, 연속 dedup 은 note_map_raw 가 이미 수행.
        history_snap = list(self._map_history)
        # 베이스 맵별 count — map_history 상 등장 횟수.
        counts_map: Dict[str, int] = {}
        for nm in history_snap:
            counts_map[nm] = counts_map.get(nm, 0) + 1
        counts_sorted: List[List] = sorted(
            ([k, v] for k, v in counts_map.items()),
            key=lambda kv: (-kv[1], kv[0]),
        )
        # 마지막 맵도 정산 — 세션 종료 시점 기준.
        if self._map_stats_cur is not None:
            prev = self._map_stats_cur
            dur_m = max(0, int(end_ts - float(prev.get("enter_ts") or end_ts)))
            ex = int(prev.get("enter_xp") or 0)
            last_xp = int(self._last_xp or self._last_cur_xp)
            gain_m = max(0, last_xp - ex) if last_xp and ex else 0
            key = str(prev.get("name") or "")
            if key:
                agg = self._map_stats_agg.get(key)
                if agg is None:
                    agg = {"name": key, "count": 0,
                           "duration_sec": 0, "xp_gain": 0}
                    self._map_stats_agg[key] = agg
                agg["count"] = int(agg["count"]) + 1
                agg["duration_sec"] = int(agg["duration_sec"]) + dur_m
                agg["xp_gain"] = int(agg["xp_gain"]) + gain_m
        # map_stats 는 등장 순서(_map_history first-seen) 기준.
        seen: set = set()
        order: List[str] = []
        for nm in history_snap:
            if nm not in seen:
                seen.add(nm)
                order.append(nm)
        map_stats: List[Dict] = []
        for nm in order:
            a = self._map_stats_agg.get(nm)
            if a is not None:
                map_stats.append(dict(a))
        rec = SessionRecord(
            session_id=self.session_id,
            start_ts=self.start_ts,
            end_ts=end_ts,
            duration_sec=dur,
            xp_gain=gain,
            laps=laps_rec,
            peak_xp_per_hour=self._peak_xph,
            map_top=top,
            map_history=history_snap,
            map_counts=counts_sorted,
            map_stats=map_stats,
        )
        try:
            self._storage.append(rec, close_reason=reason)
        except Exception:
            pass
        self._last_report = rec
        # 상태 리셋.
        self.active = False
        self.start_ts = 0.0
        self.session_id = ""
        self._start_xp = 0
        self._last_xp = self._last_cur_xp
        self._total_delta = 0
        self._map_count.clear()
        self._map_stats_agg.clear()
        self._map_stats_cur = None
        self._peak_xph = 0
        return rec

    def last_report(self) -> Optional[SessionRecord]:
        with self._lock:
            return self._last_report

    def snapshot(self) -> Dict:
        with self._lock:
            # map_stats: agg 복사 + 현재 열린 맵(_map_stats_cur) pending diff 합산.
            # 오버레이 맵 히스토리에서 "맵 | 시간 | 경험치" 표기 위한 per-base 집계.
            agg_copy: Dict[str, Dict] = {
                k: dict(v) for k, v in self._map_stats_agg.items()
            }
            if self._map_stats_cur is not None:
                cur = self._map_stats_cur
                key = str(cur.get("name") or "")
                if key:
                    now = self._now()
                    dur = max(0, int(now - float(cur.get("enter_ts") or now)))
                    ex = int(cur.get("enter_xp") or 0)
                    cxv = int(self._last_xp or self._last_cur_xp)
                    gain = max(0, cxv - ex) if cxv and ex else 0
                    a = agg_copy.get(key) or {
                        "name": key, "count": 0,
                        "duration_sec": 0, "xp_gain": 0,
                    }
                    # 현재 맵은 아직 정산 전이지만 오버레이에선 지금까지 경과/획득이
                    # 보여야 함 → count 는 그대로, dur/xp 만 더함.
                    a["duration_sec"] = int(a["duration_sec"]) + dur
                    a["xp_gain"] = int(a["xp_gain"]) + gain
                    agg_copy[key] = a
            return {
                "active": self.active,
                "session_id": self.session_id,
                "start_ts": self.start_ts,
                "duration_sec": self.cur_duration(),
                "xp_gain": self.cur_xp_gain(),
                "xp_per_hour": self.cur_xp_per_hour(),
                "peak_xp_per_hour": self._peak_xph,
                "map_top": max(self._map_count.items(), key=lambda kv: kv[1])[0]
                    if self._map_count else "",
                "map_history": list(self._map_history),
                # base name → {name, count, duration_sec, xp_gain}. 오버레이/리포트 공용.
                "map_stats": agg_copy,
                "cur_xp": int(self._last_xp),
            }


class HuntReportStorage:
    """일별 jsonl 파일 단위 append + read."""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            # 실행 경로 기준 ./hunt_reports (사용자 환경 C:\oldbaram\ 아래).
            base_dir = str(pathlib.Path.cwd() / "hunt_reports")
        self.base_dir = pathlib.Path(base_dir)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _date_path(self, d: _dt.date) -> pathlib.Path:
        return self.base_dir / f"{d.strftime('%Y-%m-%d')}.jsonl"

    def append(self, rec: SessionRecord, close_reason: str = "") -> None:
        if rec is None:
            return
        d = _dt.datetime.fromtimestamp(rec.start_ts).date()
        path = self._date_path(d)
        payload = asdict(rec)
        payload["close_reason"] = close_reason
        # ISO 시간 보조 — 사람 읽기용.
        payload["start_iso"] = _dt.datetime.fromtimestamp(
            rec.start_ts
        ).strftime("%Y-%m-%d %H:%M:%S")
        payload["end_iso"] = _dt.datetime.fromtimestamp(
            rec.end_ts
        ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False))
                f.write("\n")
        except Exception:
            pass

    def list_dates(self) -> List[str]:
        try:
            files = list(self.base_dir.glob("*.jsonl"))
        except Exception:
            return []
        dates = []
        for f in files:
            stem = f.stem
            if len(stem) == 10 and stem[4] == "-" and stem[7] == "-":
                dates.append(stem)
        dates.sort(reverse=True)
        return dates

    def read_date(self, date_str: str) -> List[Dict]:
        path = self.base_dir / f"{date_str}.jsonl"
        if not path.exists():
            return []
        out: List[Dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return out


class HuntAnalytics:
    """Attacker 가 소유하는 최상위 통합 객체."""

    def __init__(self, base_dir: Optional[str] = None,
                 idle_timeout_sec: float = 60.0):
        self.storage = HuntReportStorage(base_dir)
        self.session = HuntSession(self.storage,
                                   lap_tracker=None,
                                   idle_timeout_sec=idle_timeout_sec)
        self.laps = LapTracker(self.session)
        self.session.lap_tracker = self.laps
        self._last_map_name: str = ""
        self._last_emitted_rec_id: str = ""

    # --- 이벤트 ---
    def on_map(self, map_name: str) -> Optional[LapRecord]:
        if not map_name:
            return None
        if map_name == self._last_map_name:
            return None
        rec = self.laps.on_map_change(map_name, self.session._last_cur_xp)
        top, _ = parse_map_name(map_name)
        if top:
            self.session.note_map(top)
        # 원본 맵 이름(선비족X-X 형태) 순서대로 누적 — 오버레이 히스토리용.
        self.session.note_map_raw(map_name)
        self._last_map_name = map_name
        return rec

    def on_xp(self, xp: int) -> None:
        self.session.on_xp(xp)

    def on_xph(self, xph: int) -> None:
        self.session.note_xph(xph)

    def tick(self) -> Optional[SessionRecord]:
        return self.session.tick()

    def stop(self) -> Optional[SessionRecord]:
        return self.session.force_close()

    # --- 조회 ---
    def snapshot(self) -> Dict:
        return {
            "session": self.session.snapshot(),
            "laps": self.laps.snapshot(),
            "last_report": asdict(self.session._last_report)
                if self.session._last_report else None,
        }

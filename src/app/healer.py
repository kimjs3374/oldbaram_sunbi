"""힐러 PC 엔트리.

힐러 화면에서:
  - YOLO: 격수 위의 빨탭 검출 (화면 내 위치 → 좌우 이동 추정)
  - mss: 화면 캡처
UDP: 격수 PC 좌표/맵 수신 (맵 전환, 장거리 이동 힌트)
FSM: 빨탭 보임/안보임, 좌표 변화, 맵 전환 기반 상태 전이
입력: PostMessage/SendInput로 옛바 창에 방향키
"""
import argparse
import signal
import time
from collections import deque

from ..config import load as load_cfg
from ..capture.screen import Grabber
from ..vision.yolo import YoloRunner
from ..net.udp_receiver import UdpReceiver
from ..net.protocol import State  # noqa
from ..fsm.controller import Follower
from ..fsm.state import FsmState
from ..input.keys import KeyController, find_windows_by_process


FOLLOW_EDGE_FRAC = 0.15  # 빨탭이 화면 x중심에서 이 비율보다 좌우면 그 방향 이동
DEADZONE_FRAC = 0.05     # 중앙 deadzone


class Healer:
    def __init__(self, cfg):
        self.cfg = cfg
        hwnd = None
        if cfg.input.target_window.lower().endswith(".exe"):
            wins = find_windows_by_process(cfg.input.target_window)
            if wins:
                hwnd = wins[0]
                print(f"[healer] 창 캡처 hwnd={hwnd} ({cfg.input.target_window})")
            else:
                print(f"[healer][!] {cfg.input.target_window} 창 없음 → "
                      f"monitor_index={cfg.capture.monitor_index} fallback")
        self.grab = Grabber(cfg.capture.monitor_index, hwnd=hwnd)
        self.yolo = YoloRunner(cfg.vision.weights, imgsz=cfg.vision.imgsz,
                                conf=cfg.vision.conf, iou=cfg.vision.iou,
                                half=cfg.vision.half, device=cfg.vision.device)
        self.recv = UdpReceiver(cfg.net.bind_host, cfg.net.port)
        self.fol = Follower(red_lost_sec=cfg.fsm.red_lost_sec,
                             stuck_sec=cfg.fsm.stuck_sec,
                             dead_reckon_sec=cfg.fsm.dead_reckon_sec)
        self.keys = KeyController(window_name=cfg.input.target_window,
                                   method=cfg.input.method,
                                   keydown_ms_min=cfg.input.keydown_ms_min,
                                   keydown_ms_max=cfg.input.keydown_ms_max,
                                   jitter_ms=cfg.input.jitter_ms)
        self._stop = False
        self._red_last_seen = 0.0

    def stop(self, *_):
        self._stop = True

    def _decide_dir(self, frame_w: int, red_det, attacker_state) -> str:
        """빨탭(격수 머리 위) 화면 위치 기반으로 격수에게 접근.

        빨탭이 화면 오른쪽 → 격수가 오른쪽 → R 눌러 따라감.
        빨탭이 안 보이면 '-' (격수 키 따라하기 금지. 힐러 좌표 OCR 추가 전까진 정지).
        """
        if red_det is None:
            return "-"
        dx = red_det.cx - frame_w / 2
        dead = frame_w * DEADZONE_FRAC
        if dx > dead:
            return "R"
        if dx < -dead:
            return "L"
        return "-"

    def run(self):
        cfg = self.cfg
        print(f"[healer] listen {cfg.net.bind_host}:{cfg.net.port}")
        if self.keys.hwnd is None:
            print(f"[healer][!] 게임 창 '{cfg.input.target_window}' 못 찾음. "
                  f"SendInput fallback (포커스 필요)")
        self.recv.start()
        current_dir = "-"
        last_log = 0.0
        last_seq_logged = -1
        try:
            while not self._stop:
                frame = self.grab.grab()
                H, W = frame.shape[:2]

                # YOLO 빨탭 검출 (전체 후보 포함)
                all_dets = self.yolo.detect(frame)
                det = None
                for d in all_dets:
                    if d.w < 25 or d.h < 40:
                        continue
                    if det is None or d.conf > det.conf:
                        det = d
                if det is not None:
                    self._red_last_seen = time.time()

                # UDP 격수 상태 (없으면 dummy)
                atk = self.recv.latest()
                if atk is None:
                    atk = State()
                atk.red_tab = det is not None
                state = self.fol.update(atk)

                if state in (FsmState.FOLLOW, FsmState.COMBAT):
                    d = self._decide_dir(W, det, atk)
                    if d != current_dir:
                        self.keys.release_all()
                        if d != "-":
                            self.keys.hold(d)
                        current_dir = d
                else:
                    if current_dir != "-":
                        self.keys.release_all()
                        current_dir = "-"

                now = time.time()
                if now - last_log >= 1.0:
                    if atk is None:
                        seq = -1
                        drate = 0
                    else:
                        seq = atk.seq
                        drate = seq - last_seq_logged if last_seq_logged >= 0 else 0
                        last_seq_logged = seq
                    red_info = (f"red@({det.cx},{det.cy},{det.w}x{det.h},c={det.conf:.2f})"
                                if det is not None else "red=X")
                    # raw 검출 전부 요약 (크기 제약 전)
                    raw = ",".join(f"{d.w}x{d.h}@{d.conf:.2f}" for d in all_dets[:5])
                    raw_info = f"raw[{len(all_dets)}]={raw or '-'}"
                    udp_info = f"udp={'Y' if self.recv.latest() else 'N'}"
                    print(f"[healer] seq={seq} {udp_info} {red_info} "
                          f"fsm={state.value} hold={current_dir} {raw_info}")
                    last_log = now

                time.sleep(0.005)
        finally:
            self.keys.release_all()
            self.recv.stop()
            print("[healer] 종료")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    cfg = load_cfg() if args.config is None else load_cfg(args.config)
    app = Healer(cfg)
    signal.signal(signal.SIGINT, app.stop)
    app.run()


if __name__ == "__main__":
    main()

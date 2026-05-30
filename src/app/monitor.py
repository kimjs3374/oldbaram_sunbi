"""UDP 수신 상태만 찍는 디버그 도구. 격수↔힐러 사이 패킷 확인용."""
import argparse
import time

from ..config import load as load_cfg
from ..net.udp_receiver import UdpReceiver
from ..fsm.controller import Follower


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    cfg = load_cfg() if args.config is None else load_cfg(args.config)

    r = UdpReceiver(cfg.net.bind_host, cfg.net.port)
    f = Follower(red_lost_sec=cfg.fsm.red_lost_sec,
                  stuck_sec=cfg.fsm.stuck_sec,
                  dead_reckon_sec=cfg.fsm.dead_reckon_sec)
    r.start()
    print(f"listening {cfg.net.bind_host}:{cfg.net.port}")
    try:
        while True:
            s = r.latest()
            st = f.update(s).value if s else "NO_DATA"
            if s:
                print(f"\rseq={s.seq:>6} map={s.map_name[:12]:<12} "
                      f"coord=({s.x:>4},{s.y:>4}) valid={int(s.coord_valid)} "
                      f"red={int(s.red_tab)} dir={s.last_dir} fsm={st}   ",
                      end="", flush=True)
            else:
                print(f"\r[no data] fsm={st}    ", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        r.stop()


if __name__ == "__main__":
    main()

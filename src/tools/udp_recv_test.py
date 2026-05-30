"""UDP 수신 확인 도구. 힐러 PC에서 이걸 먼저 돌려 패킷이 도달하는지 판정.

사용:
    py -m src.tools.udp_recv_test           # port=54545
    py -m src.tools.udp_recv_test 54545     # 포트 지정

Tailscale 트러블슈팅 순서:
  1) 힐러 PC에서 `tailscale ip -4` → 100.x.y.z 확인.
  2) 격수 PC의 config.yaml `net.peers` 에 그 IP 넣기 (공인 IP 아님!).
  3) 힐러에서 이 스크립트 실행.
  4) 격수에서 `py -m src.app.healer_gui` → 격수 모드 ▶ 시작.
  5) 힐러 콘솔에 `[addr]: NN bytes` 가 찍히면 OK. 안 찍히면:
     - Windows 방화벽 (udp 54545 inbound 허용)
     - Tailscale ACL
     - peers IP 오타
"""
import socket
import sys
import time

from ..net.protocol import State


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 54545
    host = "0.0.0.0"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError as e:
        print(f"[!] bind 실패 {host}:{port} — {e}")
        print("    다른 프로세스가 이 포트를 잡고 있지 않은지, 방화벽 확인.")
        return
    print(f"listen {host}:{port} (Ctrl+C 종료)")
    print("  대기 중...")
    n = 0
    last_src = None
    last_log = time.time()
    try:
        while True:
            s.settimeout(2.0)
            try:
                data, addr = s.recvfrom(8192)
            except socket.timeout:
                now = time.time()
                if now - last_log >= 5.0:
                    print(f"  [대기] {n} 패킷 수신, 최근 src={last_src}")
                    last_log = now
                continue
            n += 1
            last_src = addr
            # 파싱 시도
            parsed = ""
            try:
                st = State.from_bytes(data)
                parsed = (f"seq={st.seq} map={st.map_name[:12]!r} "
                          f"coord=({st.x},{st.y}) valid={int(st.coord_valid)} "
                          f"dir={st.last_dir} hp={st.hp} mp={st.mp}")
            except Exception as e:
                parsed = f"[parse 실패: {e}] raw={data[:32]!r}"
            if n <= 5 or n % 30 == 0:
                print(f"#{n} {addr[0]}:{addr[1]} {len(data)}B {parsed}")
    except KeyboardInterrupt:
        print(f"\n종료. 총 {n} 패킷 수신. 마지막 송신자: {last_src}")
    finally:
        s.close()


if __name__ == "__main__":
    main()

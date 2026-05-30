"""키 입력 방향 검증 PoC.

msw.exe 창에 포커스 주고 실행. 각 방향키를 순서대로 1초씩 hold.
콘솔 안내대로 캐릭터가 움직이는지 확인.

실행: py -m src.app.poc_keytest
"""
import time

from ..config import load as load_cfg
from ..input.keys import KeyController


def main():
    cfg = load_cfg()
    keys = KeyController(window_name=cfg.input.target_window,
                         method=cfg.input.method)
    print(f"[poc] hwnd={keys.hwnd} method={cfg.input.method}")
    print("[poc] 3초 후 시작. msw.exe 창 클릭해서 포커스 주세요.")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    seq = [("R", "오른쪽"), ("L", "왼쪽"), ("U", "위"), ("D", "아래")]
    for k, name in seq:
        print(f"[poc] === {name} (key={k}) 1.2초 hold ===")
        keys.hold(k)
        time.sleep(1.2)
        keys.release(k)
        time.sleep(0.8)
    print("[poc] 끝. 각 방향이 라벨대로 움직였는지 알려주세요.")


if __name__ == "__main__":
    main()

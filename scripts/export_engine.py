"""YOLO .pt → TensorRT .engine 변환 (1회 실행).

[배경]
- PyTorch predict 가 12~484ms 로 30배 스파이크 (CUDA 13.1 드라이버 +
  torch 2.5.1+cu124 mismatch → kernel JIT 반복). yolo.py 의 [YOLO-SPIKE]
  로그에서 실측.
- TensorRT engine 은 fp16 kernel 을 사전 컴파일 → JIT 0, 스파이크 0.
  RTX 3080 에서 3~5ms 고정 예상.

[주의]
- engine 파일은 GPU architecture 종속 → **실행 PC(RTX 3080)에서 변환**
  필요. 다른 PC (GPU 다르거나 없음) 에서 만든 engine 은 쓸 수 없음.
- 변환은 한 번만. 결과는 best.pt 옆에 best.engine 로 저장.
- yolo.py 는 best.engine 있으면 자동 로드, 없으면 best.pt fallback.

[사용법]
    # 실행 PC 의 dist_dosa/ 루트에서:
    pip install tensorrt
    py scripts/export_engine.py
    # 또는 특정 가중치 지정:
    py scripts/export_engine.py C:\\oldbaram\\dataset\\runs\\full_v3\\weights\\best.pt

[소요 시간]
- 첫 export 는 3~10분 (kernel tuning). 진행 로그가 찍히므로 기다리면 됨.
- 드라이버 업그레이드하면 engine 재생성 필요할 수 있음.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> int:
    # 기본 weights 경로 — config.yaml 의 기본값과 동일.
    default_pt = (
        Path(__file__).resolve().parents[1]
        / "dataset" / "runs" / "full_v3" / "weights" / "best.pt"
    )
    pt_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_pt

    if not pt_path.exists():
        print(f"[ERROR] .pt 가중치 파일을 찾을 수 없습니다: {pt_path}")
        print("        사용: py scripts/export_engine.py [경로/best.pt]")
        return 1

    print(f"[EXPORT] 입력 가중치: {pt_path}")
    engine_path = pt_path.with_suffix(".engine")
    if engine_path.exists():
        print(f"[EXPORT] 기존 engine 발견 → 덮어씀: {engine_path}")

    # torch / CUDA 상태 확인 (디버깅 편의).
    try:
        import torch
        print(
            f"[ENV] torch={torch.__version__} "
            f"cuda_available={torch.cuda.is_available()}"
        )
        if torch.cuda.is_available():
            print(f"[ENV] device={torch.cuda.get_device_name(0)}")
        else:
            print("[WARN] CUDA 미감지 — TensorRT export 에는 GPU 필수. 중단.")
            return 2
    except Exception as e:
        print(f"[WARN] torch 확인 실패: {e}")

    # TensorRT 설치 여부 선확인.
    try:
        import tensorrt  # noqa: F401
        print(f"[ENV] tensorrt 감지됨")
    except ImportError:
        print("[ERROR] tensorrt 미설치. 먼저 'pip install tensorrt' 후 재실행.")
        return 3

    from ultralytics import YOLO

    model = YOLO(str(pt_path))

    print("[EXPORT] TensorRT engine 생성 시작 (fp16, imgsz=640, workspace=4GB)")
    print("         kernel tuning 에 3~10분 소요. 진행 로그 대기...")
    t0 = time.perf_counter()
    # ultralytics export: format="engine" → .engine 파일 생성.
    # - half=True : fp16 추론 (RTX 3080 Tensor Core 활용).
    # - imgsz=640 : 고정 입력 크기. config.yaml 의 vision.imgsz 와 일치.
    # - device=0  : 첫 번째 CUDA 디바이스.
    # - workspace : TensorRT 가 kernel tuning 에 쓸 임시 메모리 (GB).
    out = model.export(
        format="engine",
        half=True,
        imgsz=640,
        device=0,
        workspace=4,
    )
    elapsed = time.perf_counter() - t0
    print(f"[EXPORT] 완료: {out}  ({elapsed:.1f}s)")
    print(f"[EXPORT] 엔진 위치: {engine_path}")
    print("[NEXT] 이제 healer_worker 를 재시작하면 yolo.py 가 자동으로 engine 로드.")
    print("       [YOLO-INIT] backend=TensorRT 로그 확인 → 적용 성공.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

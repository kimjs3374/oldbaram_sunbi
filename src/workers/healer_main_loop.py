"""DEPRECATED — refactor-v6 회귀로 롤백. run() 본문은 healer_worker.HealerWorker.run() 으로 복원됨.

이 파일은 `from .healer_main_loop import run_frame_loop` 가 남아있는 경우를
위해 스텁만 유지. 직접 호출 시 즉시 오류.
"""


def run_frame_loop(worker):
    raise RuntimeError(
        "run_frame_loop 은 refactor-v6 회귀로 deprecated. "
        "HealerWorker.run() 직접 호출."
    )

# -*- coding: utf-8 -*-
"""main_window.py 통째 복사 + import 경로 변환.

원본: dist_dosa/src/ui/main_window.py (사용자 정본)
대상: src_v2/ui/main_window_v2.py

변환 규칙:
1) workers facade 4건 → src_v2.workers.v1_compat 의 V1Facade 로 별칭 import.
2) 그 외 모든 relative import 를 절대 import 로 치환.
   - `from ..xxx` → `from src.xxx`
   - `from .xxx` → `from src.ui.xxx`
3) 본문은 무수정.

사용:
    py -m src_v2._tools.copy_main_window
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path("dist_dosa/src/ui/main_window.py").resolve()
DST = Path("src_v2/ui/main_window_v2.py").resolve()


# 워커 4건만 facade 로 별칭. 정확 매칭 (전체 라인).
WORKER_REPLACE = {
    "from ..workers.healer_worker import HealerWorker":
        "from src_v2.workers.v1_compat import HealerWorkerV1Facade as HealerWorker",
    "from ..workers.attacker_worker import AttackerWorker":
        "from src_v2.workers.v1_compat import AttackerWorkerV1Facade as AttackerWorker",
    # heartbeat / control_listener 는 v1 그대로 사용 (src 무수정 원칙).
    "from ..workers.heartbeat import HealerHeartbeat, AttackerHeartbeat":
        "from src.workers.heartbeat import HealerHeartbeat, AttackerHeartbeat",
    "from ..workers.control_listener import ControlListener":
        "from src.workers.control_listener import ControlListener",
}


def transform_line(line: str) -> str:
    s = line.rstrip("\n")
    # 우선순위: 워커 4건 정확 치환.
    for k, v in WORKER_REPLACE.items():
        if s.strip() == k:
            return line.replace(k, v)
    # `from ..xxx import ...` → `from src.xxx import ...`
    m = re.match(r"^(\s*)from \.\.([\w.]+) import (.*)$", s)
    if m:
        indent, mod, names = m.group(1), m.group(2), m.group(3)
        return f"{indent}from src.{mod} import {names}\n"
    # `from .xxx import ...` (점 1개) → `from src.ui.xxx import ...`
    m = re.match(r"^(\s*)from \.([\w.]+) import (.*)$", s)
    if m:
        indent, mod, names = m.group(1), m.group(2), m.group(3)
        return f"{indent}from src.ui.{mod} import {names}\n"
    return line


def main() -> int:
    if not SRC.exists():
        print(f"[!] source not found: {SRC}")
        return 2
    DST.parent.mkdir(parents=True, exist_ok=True)
    text = SRC.read_text(encoding="utf-8")
    out_lines = []
    header = (
        '"""main_window_v2 — dist_dosa/src/ui/main_window.py 의 1:1 복제본.\n'
        "\n"
        "본 파일은 자동 생성됨. 수정 금지 (src_v2/_tools/copy_main_window.py 재실행).\n"
        "변경점: relative import 만 절대화, workers 2건만 v1_compat facade 로 별칭.\n"
        '"""\n'
    )
    out_lines.append(header)
    for ln in text.splitlines(keepends=True):
        out_lines.append(transform_line(ln))
    DST.write_text("".join(out_lines), encoding="utf-8")
    print(f"[ok] wrote {DST} ({DST.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

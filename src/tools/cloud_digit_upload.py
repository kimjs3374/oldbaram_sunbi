"""수집된 숫자 patch(logs/digit_collect/)를 zip 해 클라우드 업로드.

사용자 머신에서 OB_COLLECT_DIGITS=1 로 앱을 1~2분 돌려 patch 를 모은 뒤 실행.
sunbi-logs 버킷의 digits/ 폴더에 올라가며, D머신에서
`py -m src.tools.cloud_logs --pull digits` 로 받는다.

  py -m src.tools.cloud_digit_upload
"""
from __future__ import annotations

import io
import pathlib
import time
import zipfile

from ..net.cloud_sync import CloudClient

_SRC = pathlib.Path.cwd() / "logs" / "digit_collect"


def main() -> None:
    if not _SRC.exists():
        print("수집 폴더 없음: logs/digit_collect/")
        print("→ 환경변수 OB_COLLECT_DIGITS=1 로 앱을 실행해 먼저 수집하세요.")
        return
    files = [p for p in _SRC.rglob("*.png") if p.is_file()]
    if not files:
        print("수집된 patch 없음 (OB_COLLECT_DIGITS=1 로 앱 실행 후 사냥 필요).")
        return

    # 라벨별 개수 요약.
    by_label: dict = {}
    for p in files:
        key = p.parent.name
        by_label[key] = by_label.get(key, 0) + 1
    print("수집 현황:")
    for k in sorted(by_label):
        print(f"   {k}: {by_label[k]}장")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(_SRC))
    ts = time.strftime("%Y%m%d_%H%M%S")
    tmp = _SRC.parent / f"digits_{ts}.zip"
    tmp.write_bytes(buf.getvalue())

    c = CloudClient()
    key = c.upload_log("digits", tmp)
    print(f"\n업로드 완료: {key}  ({len(files)}장, {tmp.stat().st_size} B)")
    try:
        tmp.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()

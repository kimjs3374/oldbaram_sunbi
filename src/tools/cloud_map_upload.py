"""수집된 맵바 crop(logs/map_crops/)을 zip 해 클라우드 업로드.

맵 OCR 재설계용 학습 데이터 수집. 격수/힐러가 사냥하면 ocr._crop_map 이
logs/map_crops/ 에 맵바 crop 을 자동 저장(20프레임마다 1장). 게임 후 실행:

  py -m src.tools.cloud_map_upload

sunbi-logs 버킷의 mapcrops/ 폴더에 올라가며, D머신에서
`py -m src.tools.cloud_logs --pull mapcrops` 로 받아 라벨링→CRNN 학습.
"""
from __future__ import annotations

import io
import pathlib
import time
import zipfile

from ..net.cloud_sync import CloudClient

_SRC = pathlib.Path.cwd() / "logs" / "map_crops"


def main() -> None:
    if not _SRC.exists():
        print("수집 폴더 없음: logs/map_crops/")
        print("→ 앱을 한 판 돌리면 ocr._crop_map 이 자동 저장합니다.")
        return
    files = [p for p in _SRC.rglob("*.png") if p.is_file()]
    if not files:
        print("수집된 맵 crop 없음 (앱 실행 후 여러 맵을 돌아다녀야 함).")
        return

    print(f"수집 현황: {len(files)}장")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(_SRC))
    ts = time.strftime("%Y%m%d_%H%M%S")
    tmp = _SRC.parent / f"mapcrops_{ts}.zip"
    tmp.write_bytes(buf.getvalue())

    c = CloudClient()
    key = c.upload_log("mapcrops", tmp)
    print(f"\n업로드 완료: {key}  ({len(files)}장, {tmp.stat().st_size} B)")
    try:
        tmp.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()

"""Supabase 업로더 (D머신/배포자 전용).

배포 대상 파일을 증분 업로드(바뀐 것만)하고 releases 테이블에 새 버전을 만든다.
앱(C머신)은 이 릴리스의 manifest 를 보고 바뀐 파일만 내려받는다.

admin 설정: ~/.oldbaram_cloud_admin.json   (repo/채팅 노출 금지)
  {
    "url": "https://<project>.supabase.co",
    "service_key": "<service_role key>",
    "bucket": "sunbi-releases"
  }

사용:
  py -m src.tools.cloud_uploader --changelog "격수 OCR 지연 수정"
  py -m src.tools.cloud_uploader --dry-run        # 업로드 없이 변경분만 출력
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import List

import requests

from ..net.cloud_sync import sha256_file

_ADMIN_PATH = pathlib.Path.home() / ".oldbaram_cloud_admin.json"
_ROOT = pathlib.Path(__file__).resolve().parents[2]  # dist_dosa = 배포 루트
_TIMEOUT = 300

# 배포 포함 대상 (repo flat 구조와 동일: 루트 = 실행 루트)
_INCLUDE_DIRS = ["src", "models"]
_INCLUDE_FILES = [
    "config.yaml",
    "knownmaps.txt",
    "requirements.txt",
    # nano 재학습 모델 + ONNX(저사양 CPU 추론). 좌표 CNN(digit_cnn.onnx)은
    # src/vision/ 아래라 _INCLUDE_DIRS 의 src rglob 으로 자동 포함.
    "dataset/runs/full_v3_nano/weights/best.pt",
    "dataset/runs/full_v3_nano/weights/best.onnx",
]
_EXCLUDE_SUFFIX = (".pyc", ".bak", ".md")
_EXCLUDE_PARTS = ("__pycache__",)


def load_admin() -> dict:
    if not _ADMIN_PATH.exists():
        sys.exit(f"[에러] admin 설정 없음: {_ADMIN_PATH}\n"
                 f"      url/service_key/bucket 을 넣어 만드세요.")
    d = json.loads(_ADMIN_PATH.read_text(encoding="utf-8"))
    for k in ("url", "service_key", "bucket"):
        if not d.get(k):
            sys.exit(f"[에러] admin 설정 항목 누락: {k}")
    d["url"] = str(d["url"]).rstrip("/")
    return d


def collect_files() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for d in _INCLUDE_DIRS:
        base = _ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix in _EXCLUDE_SUFFIX:
                continue
            if any(part in _EXCLUDE_PARTS for part in p.parts):
                continue
            if ".bak" in p.name:  # .bak / .bak.v6 / .bak.pre_rollback 등 백업 전부
                continue
            if ("easyocr" in p.parts
                    or "korean_PP-OCRv5_mobile_rec" in p.parts):
                # 2026-06-11 PaddleOCR/EasyOCR 제거 → 죽은 모델 디렉토리.
                # 사용자 머신에 잔존해도 업로드/배포에서 제외(경량화).
                continue
            out.append(p)
    for f in _INCLUDE_FILES:
        p = _ROOT / f
        if p.is_file():
            out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changelog", default="", help="이번 버전 변경 내역")
    ap.add_argument("--dry-run", action="store_true", help="업로드 없이 변경분만 표시")
    args = ap.parse_args()

    adm = load_admin()
    url, key, bucket = adm["url"], adm["service_key"], adm["bucket"]
    H = {"apikey": key, "Authorization": f"Bearer {key}"}

    # 기존 최신 manifest (증분 비교 기준)
    r = requests.get(
        f"{url}/rest/v1/releases",
        headers=H,
        params={"select": "version,manifest", "order": "version.desc", "limit": "1"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    prev_ver = rows[0]["version"] if rows else 0
    prev = {e["path"]: e["sha256"] for e in (rows[0]["manifest"] if rows else [])}

    files = collect_files()
    manifest: List[dict] = []
    changed: List[pathlib.Path] = []
    for p in files:
        rel = p.relative_to(_ROOT).as_posix()
        digest = sha256_file(p)
        manifest.append({"path": rel, "sha256": digest, "size": p.stat().st_size})
        if prev.get(rel) != digest:
            changed.append(p)

    print(f"전체 {len(files)} 파일 / 변경 {len(changed)} 파일 (이전 v{prev_ver})")
    for p in changed:
        print(f"  변경: {p.relative_to(_ROOT).as_posix()} ({p.stat().st_size} B)")

    if args.dry_run:
        print("[dry-run] 업로드/릴리스 생략.")
        return
    if not changed and rows:
        print("변경 없음 → 새 릴리스 생략.")
        return

    # 변경분 업로드 (upsert)
    for p in changed:
        rel = p.relative_to(_ROOT).as_posix()
        hu = dict(H)
        hu["x-upsert"] = "true"
        hu["Content-Type"] = "application/octet-stream"
        last_err = None
        for attempt in range(4):
            try:
                with open(p, "rb") as f:
                    ru = requests.post(
                        f"{url}/storage/v1/object/{bucket}/{rel}",
                        headers=hu, data=f, timeout=_TIMEOUT,
                    )
                ru.raise_for_status()
                last_err = None
                break
            except requests.RequestException as e:  # 504/네트워크 일시 오류 재시도
                last_err = e
                if attempt < 3:
                    print(f"  재시도({attempt + 1}/3) {rel}: {e}")
                    time.sleep(2 * (attempt + 1))
        if last_err is not None:
            raise last_err
        print(f"  업로드 완료: {rel}")

    # 새 릴리스 생성
    new_ver = prev_ver + 1
    hr = dict(H)
    hr["Content-Type"] = "application/json"
    hr["Prefer"] = "return=minimal"
    rr = requests.post(
        f"{url}/rest/v1/releases",
        headers=hr,
        data=json.dumps(
            {"version": new_ver, "changelog": args.changelog, "manifest": manifest},
            ensure_ascii=False,
        ).encode("utf-8"),
        timeout=_TIMEOUT,
    )
    rr.raise_for_status()
    print(f"[OK] 릴리스 v{new_ver} 생성 완료. 업로드 {len(changed)}개 / 총 {len(files)}개.")


if __name__ == "__main__":
    main()

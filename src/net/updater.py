"""앱 자동 업데이트: Supabase releases 기준 버전 비교 + 증분 다운로드 + 재시작.

- 실행 루트 = src 의 부모 (= 배포 루트, repo flat 구조와 동일).
- 로컬 버전 파일: <ROOT>/.version (정수). 없으면 0.
- 변경 파일만 <ROOT>/.update_staging 에 받은 뒤, 앱 종료 후 _apply_update.bat 이
  staging→ROOT 복사 + .version 갱신 + 재실행 (실행 중 파일 잠금 회피).
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Callable, List, Optional

from .cloud_sync import CloudClient, compute_updates

ROOT = pathlib.Path(__file__).resolve().parents[2]
_VERSION_FILE = ROOT / ".version"
_STAGING = ROOT / ".update_staging"


def local_version() -> int:
    try:
        return int((_VERSION_FILE.read_text(encoding="utf-8").strip() or "0"))
    except (FileNotFoundError, ValueError):
        return 0


def write_version(v: int) -> None:
    _VERSION_FILE.write_text(str(int(v)), encoding="utf-8")


def check(client: CloudClient) -> Optional[dict]:
    """원격 최신 버전 > 로컬이면 release dict, 아니면 None."""
    rel = client.latest_release()
    if rel and int(rel.get("version", 0)) > local_version():
        return rel
    return None


def download_updates(client: CloudClient, release: dict,
                     log: Optional[Callable[[str], None]] = None) -> List[dict]:
    """변경(또는 없음) 파일만 staging 에 내려받음. 반환: 받은 entry 목록."""
    manifest = release.get("manifest", [])
    todo = compute_updates(ROOT, manifest)
    if _STAGING.exists():
        shutil.rmtree(_STAGING, ignore_errors=True)
    for entry in todo:
        rel = entry["path"]
        data = client.download(rel)
        dst = _STAGING / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        if log:
            log(f"받음: {rel} ({len(data)} B)")
    return todo


def make_apply_script(new_version: int) -> pathlib.Path:
    """staging→ROOT 복사 + .version 갱신 + healer_gui 재시작 .bat 생성."""
    bat = ROOT / "_apply_update.bat"
    bat.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'xcopy /e /y /i /q "{_STAGING}\\*" "{ROOT}\\" >nul\r\n'
        f'echo {int(new_version)}> "{_VERSION_FILE}"\r\n'
        f'rmdir /s /q "{_STAGING}"\r\n'
        f'cd /d "{ROOT}"\r\n'
        'start "" py -m src.app.healer_gui\r\n',
        encoding="utf-8",
    )
    return bat


def launch_apply_and_exit(new_version: int) -> pathlib.Path:
    """적용 스크립트를 새 콘솔로 실행. 호출 측이 직후 앱을 종료해야 함."""
    bat = make_apply_script(new_version)
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
    return bat

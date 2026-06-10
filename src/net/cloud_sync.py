"""Supabase 클라우드 동기화 — 자동 업데이트(manifest/파일) + 설정 push/pull.

키 설정 파일: ~/.oldbaram_cloud.json  (repo 에 포함하지 않음)
  {
    "url": "https://<project>.supabase.co",
    "anon_key": "<anon public key>",
    "bucket": "sunbi-releases"
  }
service_role key 는 여기 두지 않는다 — 업로더(tools/cloud_uploader.py) 전용.

REST 직접 호출(requests). supabase-py 의존성 불필요.
- releases 테이블: 버전/변경내역/manifest 조회 (select, anon)
- Storage public 버킷: 파일 다운로드
- app_settings 테이블: 설정 pull/push (upsert)
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any, List, Optional

import requests

_CFG_PATH = pathlib.Path.home() / ".oldbaram_cloud.json"
_TIMEOUT = 30
LOG_BUCKET = "sunbi-logs"  # 디버그 로그 전용 버킷 (anon insert/select 정책 필요)


class CloudConfigError(RuntimeError):
    """클라우드 설정 파일 누락/불완전."""


def config_path() -> pathlib.Path:
    return _CFG_PATH


def load_config() -> dict:
    if not _CFG_PATH.exists():
        raise CloudConfigError(f"클라우드 설정 없음: {_CFG_PATH}")
    data = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    for k in ("url", "anon_key", "bucket"):
        if not data.get(k):
            raise CloudConfigError(f"설정 항목 누락: {k}")
    data["url"] = str(data["url"]).rstrip("/")
    return data


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class CloudClient:
    """Supabase anon 클라이언트 (읽기 + 설정 upsert)."""

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or load_config()
        self.url = self.cfg["url"]
        self.key = self.cfg["anon_key"]
        self.bucket = self.cfg["bucket"]

    @property
    def _headers(self) -> dict:
        return {"apikey": self.key, "Authorization": f"Bearer {self.key}"}

    # ---------- 릴리스/버전 ----------
    def latest_release(self) -> Optional[dict]:
        """releases 최신 1건. 없으면 None.

        반환 예: {"version": 3, "changelog": "...",
                  "manifest": [{"path": "src/...", "sha256": "..", "size": 123}, ...]}
        """
        r = requests.get(
            f"{self.url}/rest/v1/releases",
            headers=self._headers,
            params={
                "select": "version,changelog,manifest",
                "order": "version.desc",
                "limit": "1",
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

    def download(self, path: str) -> bytes:
        """public 버킷에서 파일 1개 다운로드 (바이트)."""
        r = requests.get(
            f"{self.url}/storage/v1/object/public/{self.bucket}/{path}",
            headers=self._headers,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.content

    # ---------- 설정 동기화 ----------
    def pull_settings(self, sid: str) -> Optional[dict]:
        """app_settings[id=sid].data 반환. 없으면 None."""
        r = requests.get(
            f"{self.url}/rest/v1/app_settings",
            headers=self._headers,
            params={"id": f"eq.{sid}", "select": "data"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0]["data"] if rows else None

    def push_settings(self, sid: str, role: str, data: dict) -> None:
        """app_settings upsert (id 충돌 시 갱신)."""
        h = dict(self._headers)
        h["Content-Type"] = "application/json"
        h["Prefer"] = "resolution=merge-duplicates,return=minimal"
        r = requests.post(
            f"{self.url}/rest/v1/app_settings",
            headers=h,
            params={"on_conflict": "id"},
            data=json.dumps({"id": sid, "role": role, "data": data}),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()

    # ---------- 디버그 로그 (sunbi-logs 버킷) ----------
    def upload_log(self, sid: str, local_path) -> str:
        """로그 파일을 sunbi-logs/{sid}/{filename} 으로 업로드. 반환: 저장 key."""
        name = pathlib.Path(local_path).name
        # Supabase storage key 는 ASCII 만 허용 → 한글(닉) 등 제거.
        # (닉은 로그 파일 내부 헤더 nick=... 로 식별 가능)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "", name) or "session.log"
        key = f"{sid}/{safe}"
        h = dict(self._headers)
        h["x-upsert"] = "true"
        h["Content-Type"] = "text/plain; charset=utf-8"
        with open(local_path, "rb") as f:
            r = requests.post(
                f"{self.url}/storage/v1/object/{LOG_BUCKET}/{key}",
                headers=h, data=f, timeout=120,
            )
        r.raise_for_status()
        return key

    def list_logs(self, prefix: str = "") -> list:
        """sunbi-logs 의 prefix 하위 목록. prefix='' → sid 폴더들,
        prefix='healer-0/' → 그 안 파일들. 각 entry: {name, id, metadata, ...}."""
        h = dict(self._headers)
        h["Content-Type"] = "application/json"
        r = requests.post(
            f"{self.url}/storage/v1/object/list/{LOG_BUCKET}",
            headers=h,
            data=json.dumps({
                "prefix": prefix, "limit": 1000,
                "sortBy": {"column": "name", "order": "desc"},
            }),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    def download_log(self, key: str) -> bytes:
        r = requests.get(
            f"{self.url}/storage/v1/object/public/{LOG_BUCKET}/{key}",
            headers=self._headers, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.content


def compute_updates(root: pathlib.Path, manifest: List[dict]) -> List[dict]:
    """manifest 와 로컬(root) 비교 → 새로 받아야 할 entry 목록.

    로컬에 없거나 sha256 이 다른 파일만 반환. (git 없는 증분 다운로드)
    """
    todo: List[dict] = []
    for entry in manifest:
        rel = entry.get("path")
        if not rel:
            continue
        p = root / rel
        if not p.exists() or sha256_file(p) != entry.get("sha256"):
            todo.append(entry)
    return todo

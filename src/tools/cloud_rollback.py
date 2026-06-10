"""Supabase 릴리스 롤백 (D머신/배포자, secret key).

특정 버전 release row 삭제 → 자동으로 이전 버전이 최신.
storage 파일은 그대로 유지(증분 upsert) → 이전 버전 manifest 로 즉시 복구.

사용:
  py -m src.tools.cloud_rollback --list           # 릴리스 목록
  py -m src.tools.cloud_rollback --delete 6        # v6 하나만 삭제 → v5 최신
  py -m src.tools.cloud_rollback --to 4            # v4 까지 롤백 (v5,v6 모두 삭제)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import requests

_ADMIN_PATH = pathlib.Path.home() / ".oldbaram_cloud_admin.json"
_TIMEOUT = 60


def load_admin() -> dict:
    if not _ADMIN_PATH.exists():
        sys.exit(f"[에러] admin 설정 없음: {_ADMIN_PATH}")
    d = json.loads(_ADMIN_PATH.read_text(encoding="utf-8"))
    for k in ("url", "service_key"):
        if not d.get(k):
            sys.exit(f"[에러] admin 항목 누락: {k}")
    d["url"] = str(d["url"]).rstrip("/")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="릴리스 목록")
    ap.add_argument("--delete", type=int, default=None, help="이 버전 하나 삭제")
    ap.add_argument("--to", type=int, default=None,
                    help="이 버전까지 롤백 (초과 버전 모두 삭제)")
    args = ap.parse_args()

    adm = load_admin()
    url, key = adm["url"], adm["service_key"]
    H = {"apikey": key, "Authorization": f"Bearer {key}"}

    r = requests.get(
        f"{url}/rest/v1/releases", headers=H,
        params={"select": "version,changelog", "order": "version.desc"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    print("=== releases (최신순) ===")
    for e in rows:
        print(f"  v{e['version']}: {e.get('changelog', '')}")
    versions = [e["version"] for e in rows]

    if args.to is not None:
        targets = sorted([v for v in versions if v > args.to], reverse=True)
        if not targets:
            print(f"\n이미 v{args.to} 이하입니다. 삭제할 것 없음.")
            return
    elif args.delete is not None:
        if args.delete not in versions:
            sys.exit(f"[에러] v{args.delete} 없음")
        targets = [args.delete]
    else:
        return

    remain = [v for v in versions if v not in targets]
    new_latest = max(remain) if remain else None
    print(f"\n삭제 대상: {['v'+str(t) for t in targets]} → 최신은 "
          f"{'v'+str(new_latest) if new_latest else '(없음)'}")

    hd = dict(H)
    hd["Prefer"] = "return=minimal"
    for t in targets:
        rd = requests.delete(
            f"{url}/rest/v1/releases", headers=hd,
            params={"version": f"eq.{t}"}, timeout=_TIMEOUT,
        )
        rd.raise_for_status()
        print(f"  [OK] v{t} 삭제")
    print(f"→ 롤백 완료. 사용자가 install.bat 재실행하면 "
          f"v{new_latest} 로 복구됩니다. (storage 파일 유지)")


if __name__ == "__main__":
    main()

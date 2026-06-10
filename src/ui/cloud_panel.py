"""healer_gui 클라우드 패널: 설정 sync(올리기/내리기) + 업데이트 알림/적용.

main_window.__init__ 에서 attach(self, root_layout) 1회 호출.
클라우드 설정(~/.oldbaram_cloud.json) 이 없으면 조용히 비활성(에러 팝업 없음).

설정 동기화 id = role + healer_idx:
  격수 → "attacker", 힐러 → "healer-0", "healer-1" ...
  (IP/닉이 아니라 idx 기준 → IP 바뀌어도 설정 복원)
"""
from __future__ import annotations

import json
import pathlib

from PyQt5 import QtWidgets

from . import settings_io
from ..net import cloud_sync, updater

_LOG_DIR = pathlib.Path(__file__).resolve().parents[2] / "logs"


def make_sid(role: str, idx: int) -> str:
    return "attacker" if role == "attacker" else f"healer-{int(idx)}"


def _latest_log():
    if not _LOG_DIR.is_dir():
        return None
    logs = sorted(_LOG_DIR.glob("*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _upload_current_log(mw):
    """현재 세션(가장 최근) 로그를 sunbi-logs/{role}-{ip끝자리}/ 로 업로드.

    storage 경로는 ASCII 만 가능 → role + IP 끝자리(PC 구분)로. 닉은 로그 헤더.
    """
    from ..utils.logger_setup import local_ip_suffix
    c = cloud_sync.CloudClient()
    sid = f"{mw.role}-{local_ip_suffix()}"
    p = _latest_log()
    if p is None:
        return None
    return c.upload_log(sid, str(p))


def auto_upload_log(mw) -> None:
    """closeEvent 등에서 조용히 호출 (실패 무시)."""
    try:
        _upload_current_log(mw)
    except Exception:
        pass


def attach(mw, root_layout) -> None:
    """클라우드 버튼 행을 root_layout 에 추가하고 핸들러를 연결."""
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(4)
    btn_up = QtWidgets.QPushButton("☁ 설정 올리기")
    btn_down = QtWidgets.QPushButton("☁ 설정 내리기")
    btn_log = QtWidgets.QPushButton("☁ 로그 올리기")
    lbl = QtWidgets.QLabel("")
    btn_update = QtWidgets.QPushButton("⬇ 업데이트")
    btn_update.setVisible(False)
    for b in (btn_up, btn_down, btn_log, btn_update):
        b.setMinimumHeight(22)
    row.addWidget(btn_up)
    row.addWidget(btn_down)
    row.addWidget(btn_log)
    row.addWidget(lbl, 1)
    row.addWidget(btn_update)
    root_layout.addLayout(row)
    mw._cloud_lbl = lbl
    mw._cloud_btn_update = btn_update

    def _say(t: str) -> None:
        lbl.setText(t)

    def _sid() -> str:
        # 설정 sync 는 app_settings(DB 테이블) → 한글 닉 id 가능.
        # 시작 시 입력한 닉이 있으면 닉을 id 로(직관적), 없으면 role+idx.
        # (로그 업로드는 storage 라 한글 불가 → 별도로 role+idx 사용)
        nick = getattr(mw, "_session_nick", "")
        return nick if nick else make_sid(mw.role, mw.healer_idx_spin.value())

    def on_up() -> None:
        try:
            c = cloud_sync.CloudClient()
            sid = _sid()
            c.push_settings(sid, mw.role, settings_io.collect(mw))
            _say(f"올림 완료 ({sid})")
        except cloud_sync.CloudConfigError:
            _say("클라우드 미설정 (~/.oldbaram_cloud.json)")
        except Exception as e:  # noqa: BLE001 — UI 라벨로만 보고
            _say(f"올리기 실패: {e}")

    def on_down() -> None:
        try:
            c = cloud_sync.CloudClient()
            sid = _sid()
            data = c.pull_settings(sid)
            if not data:
                _say(f"클라우드에 설정 없음 ({sid})")
                return
            mw._settings_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            settings_io.load(mw)
            _say(f"내림 완료 ({sid})")
        except cloud_sync.CloudConfigError:
            _say("클라우드 미설정 (~/.oldbaram_cloud.json)")
        except Exception as e:  # noqa: BLE001
            _say(f"내리기 실패: {e}")

    def on_update() -> None:
        try:
            c = cloud_sync.CloudClient()
            rel = updater.check(c)
            if not rel:
                _say("이미 최신")
                btn_update.setVisible(False)
                return
            _say("업데이트 다운로드 중...")
            QtWidgets.QApplication.processEvents()
            updater.download_updates(c, rel)
            updater.launch_apply_and_exit(int(rel["version"]))
            mw.close()
        except Exception as e:  # noqa: BLE001
            _say(f"업데이트 실패: {e}")

    def on_log() -> None:
        try:
            key = _upload_current_log(mw)
            _say(f"로그 올림: {key}" if key else "로그 파일 없음")
        except cloud_sync.CloudConfigError:
            _say("클라우드 미설정 (~/.oldbaram_cloud.json)")
        except Exception as e:  # noqa: BLE001
            _say(f"로그 올리기 실패: {e}")

    btn_up.clicked.connect(on_up)
    btn_down.clicked.connect(on_down)
    btn_log.clicked.connect(on_log)
    btn_update.clicked.connect(on_update)

    # 시작 시 업데이트 체크 (빠른 단일 쿼리; 미설정/실패는 조용히)
    try:
        c = cloud_sync.CloudClient()
        rel = updater.check(c)
        if rel:
            cl = (rel.get("changelog") or "").strip()
            _say(f"새 버전 v{rel['version']}" + (f": {cl}" if cl else " 있음"))
            btn_update.setVisible(True)
        else:
            _say(f"최신 (v{updater.local_version()})")
    except cloud_sync.CloudConfigError:
        _say("클라우드 미설정")
    except Exception:  # noqa: BLE001 — 시작 시 네트워크 실패 무시
        _say("")

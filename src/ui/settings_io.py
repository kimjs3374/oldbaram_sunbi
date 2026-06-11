"""MainWindow 설정 I/O.

`~/.oldbaram_gui.json` 저장/복원. MainWindow 메서드로는 `_collect_settings`,
`_save_settings`, `_load_settings` 가 각각 collect/save/load 에 위임.

JSON 스키마 무변경 — 모든 위젯 상태 키 보존.
"""
from __future__ import annotations

import json
import os

def collect(mw) -> dict:
    return {
        "role": mw.role,
        "attacker_subclass": getattr(mw, "attacker_subclass", "thief"),
        "attacker_rank": int(getattr(mw, "attacker_rank", 4)),
        "arm": mw.chk_arm.isChecked(),
        "follow_only": mw.chk_follow_only.isChecked(),
        # Param
        "conf": mw.conf_slider.value(),
        "min_w": mw.minw_spin.value(),
        "min_h": mw.minh_spin.value(),
        "tol": mw.tol_spin.value(),
        "yn": mw.yn_spin.value(),
        # Network
        "peers": mw.peers_edit.text(),
        "port": mw.port_spin.value(),
        "rate": mw.rate_spin.value(),
        "healer_idx": mw.healer_idx_spin.value(),
        # Cooldown region
        "cd_region_x": int(mw.cfg.cooldown.region_x),
        "cd_region_y": int(mw.cfg.cooldown.region_y),
        "cd_region_w": int(mw.cfg.cooldown.region_w),
        "cd_region_h": int(mw.cfg.cooldown.region_h),
        # Nickname region
        "nick_region_x": int(getattr(
            mw.cfg.cooldown, "nick_region_x", -1)),
        "nick_region_y": int(getattr(
            mw.cfg.cooldown, "nick_region_y", -1)),
        "nick_region_w": int(getattr(
            mw.cfg.cooldown, "nick_region_w", 0)),
        "nick_region_h": int(getattr(
            mw.cfg.cooldown, "nick_region_h", 0)),
        # Buff(파력무참) region
        "buff_region_x": int(getattr(
            mw.cfg.cooldown, "buff_region_x", -1)),
        "buff_region_y": int(getattr(
            mw.cfg.cooldown, "buff_region_y", -1)),
        "buff_region_w": int(getattr(
            mw.cfg.cooldown, "buff_region_w", 0)),
        "buff_region_h": int(getattr(
            mw.cfg.cooldown, "buff_region_h", 0)),
        # HP/MP region (2026-04-20 신규) — 양측 PC 모두 OCR 사용.
        "hp_region_x": int(getattr(
            mw.cfg.cooldown, "hp_region_x", -1)),
        "hp_region_y": int(getattr(
            mw.cfg.cooldown, "hp_region_y", -1)),
        "hp_region_w": int(getattr(
            mw.cfg.cooldown, "hp_region_w", 0)),
        "hp_region_h": int(getattr(
            mw.cfg.cooldown, "hp_region_h", 0)),
        "mp_region_x": int(getattr(
            mw.cfg.cooldown, "mp_region_x", -1)),
        "mp_region_y": int(getattr(
            mw.cfg.cooldown, "mp_region_y", -1)),
        "mp_region_w": int(getattr(
            mw.cfg.cooldown, "mp_region_w", 0)),
        "mp_region_h": int(getattr(
            mw.cfg.cooldown, "mp_region_h", 0)),
        # 사용자 입력 최대 HP/MP (OCR cur+max 분리 + pct 환산용).
        "hp_max": int(mw.hp_max_spin.value())
            if hasattr(mw, "hp_max_spin") else 0,
        "mp_max": int(mw.mp_max_spin.value())
            if hasattr(mw, "mp_max_spin") else 0,
        # 공력증강 임계치 (2026-04-20).
        "gyoungryeok_mp_thr": int(
            mw.skill_dlg.gyoungryeok_mp_spin.value()),
        # F11 통합 실행 (A+B) 토글 (2026-04-20 Patch 2.12).
        "f11_ab_combined": bool(
            mw.skill_dlg.chk_f11_ab_combined.isChecked())
            if hasattr(mw.skill_dlg, "chk_f11_ab_combined") else True,
        # Overlay
        "overlay_on": mw.chk_overlay.isChecked(),
        "overlay_opacity": int(mw.slider_overlay_opacity.value())
            if hasattr(mw, "slider_overlay_opacity") else 90,
        # 스킬범위 오버레이 (격수 전용).
        "skill_range_on": bool(mw.chk_skill_range.isChecked())
            if hasattr(mw, "chk_skill_range") else False,
        "skill_range_tile": int(mw.spin_skill_tile_w.value())
            if hasattr(mw, "spin_skill_tile_w") else 32,
        "skill_range_tile_w": int(mw.spin_skill_tile_w.value())
            if hasattr(mw, "spin_skill_tile_w") else 32,
        "skill_range_tile_h": int(mw.spin_skill_tile_h.value())
            if hasattr(mw, "spin_skill_tile_h") else 32,
        # 공용 Y오프셋 제거됨 — 방향별 X/Y 오프셋이 대체. 키는 0 고정 저장.
        "skill_range_y_offset": 0,
        "skill_range_u_x": int(mw.spin_skill_u_x.value())
            if hasattr(mw, "spin_skill_u_x") else 0,
        "skill_range_u_y": int(mw.spin_skill_u_y.value())
            if hasattr(mw, "spin_skill_u_y") else 0,
        "skill_range_d_x": int(mw.spin_skill_d_x.value())
            if hasattr(mw, "spin_skill_d_x") else 0,
        "skill_range_d_y": int(mw.spin_skill_d_y.value())
            if hasattr(mw, "spin_skill_d_y") else 0,
        "skill_range_l_x": int(mw.spin_skill_l_x.value())
            if hasattr(mw, "spin_skill_l_x") else 0,
        "skill_range_l_y": int(mw.spin_skill_l_y.value())
            if hasattr(mw, "spin_skill_l_y") else 0,
        "skill_range_r_x": int(mw.spin_skill_r_x.value())
            if hasattr(mw, "spin_skill_r_x") else 0,
        "skill_range_r_y": int(mw.spin_skill_r_y.value())
            if hasattr(mw, "spin_skill_r_y") else 0,
        "skill_range_alpha": (
            {nm: int(sld.value())
             for nm, sld in mw.sld_skill_alpha.items()}
            if hasattr(mw, "sld_skill_alpha") else {}
        ),
        "skill_range_enabled": (
            {nm: bool(chk.isChecked())
             for nm, chk in mw.chk_skill_enabled.items()}
            if hasattr(mw, "chk_skill_enabled") else {}
        ),
        # Skill — 기원
        "rb_bonghwang": mw.skill_dlg.rb_bonghwang.isChecked(),
        "rb_shinryoung": mw.skill_dlg.rb_shinryoung.isChecked(),
        "spin_bonghwang": mw.skill_dlg.spin_bonghwang.value(),
        "spin_shinryoung": mw.skill_dlg.spin_shinryoung.value(),
        "chk_honmasul": mw.skill_dlg.chk_honmasul.isChecked(),
        "spin_honmasul": mw.skill_dlg.spin_honmasul.value(),
        # Skill — 조건부
        "skill_chks": {n: c.isChecked()
                        for n, c in mw.skill_chks.items()},
        "skill_spins": {n: sp.value()
                         for n, sp in mw.skill_spins.items()},
        "parlyuk_offset": mw.parlyuk_spin.value(),
        "parlyuk_maps": mw.parlyuk_maps_edit.text(),
        # 쩔캐 (2026-06-12). role은 "healer"로 저장되므로 jjeol 별도 키.
        "jjeol": bool(getattr(mw, "jjeol", False)),
        "jipok_hyeonin": bool(mw.chk_hyeonin.isChecked())
            if hasattr(mw, "chk_hyeonin") else False,
        "spin_jipok_gyoung": int(mw.spin_jipok_gyoung.value())
            if hasattr(mw, "spin_jipok_gyoung") else 3,
        "spin_jipok_jipok": int(mw.spin_jipok_jipok.value())
            if hasattr(mw, "spin_jipok_jipok") else 4,
        "jipok_maps": mw.jipok_maps_edit.text()
            if hasattr(mw, "jipok_maps_edit") else "",
        # Window geometry
        "win_x": int(mw.x()),
        "win_y": int(mw.y()),
        "win_w": int(mw.width()),
        "win_h": int(mw.height()),
        # 6개 추가 영역 (game/map/coord/xp/hp/mp) — 절대 좌표 리스트.
        "regions": {
            k: list(v) for k, v in mw._regions.items()
        },
        "region_overlay_on": mw.chk_region_overlay.isChecked(),
        "overlay_edit_on": mw.chk_overlay_edit.isChecked()
            if hasattr(mw, "chk_overlay_edit") else False,
        "overlay_positions": {
            k: list(v) for k, v in mw._overlay_positions.items()
        },
        # 네트워크 닉네임 목록 (peers와 동일 개수 가정).
        "nicks": list(mw.net_dlg.get_nicks())
            if hasattr(mw.net_dlg, "get_nicks") else [],
        "low_spec": mw.chk_low_spec.isChecked()
            if hasattr(mw, "chk_low_spec") else False,
        "tab_index": mw.tabs.currentIndex()
            if hasattr(mw, "tabs") else 0,
    }

def save(mw):
    try:
        data = mw._collect_settings()
        mw._settings_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        try:
            mw._append_log(f"[settings] 저장 실패: {e}")
        except Exception:
            pass

def load(mw):
    if not mw._settings_path.exists():
        return
    try:
        data = json.loads(mw._settings_path.read_text(encoding="utf-8"))
    except Exception:
        return
    g = data.get
    # 역할 — 쩔캐는 role="healer" + jjeol=True 조합으로 저장됨.
    role = g("role", "healer")
    if role == "attacker":
        mw.rb_attacker.setChecked(True)
        mw.role = "attacker"
    elif bool(g("jjeol", False)) and hasattr(mw, "rb_jjeol"):
        mw.rb_jjeol.setChecked(True)
        mw.role = "healer"
        mw.jjeol = True
    else:
        mw.rb_healer.setChecked(True)
        mw.role = "healer"
    # 격수 서브클래스 복원 (도적/전사).
    sub = str(g("attacker_subclass", "thief") or "thief")
    if sub not in ("thief", "warrior"):
        sub = "thief"
    mw.attacker_subclass = sub
    try:
        mw.rb_thief.blockSignals(True)
        mw.rb_warrior.blockSignals(True)
        if sub == "warrior":
            mw.rb_warrior.setChecked(True)
        else:
            mw.rb_thief.setChecked(True)
    finally:
        mw.rb_thief.blockSignals(False)
        mw.rb_warrior.blockSignals(False)
    try:
        if mw._helper_overlay is not None:
            mw._helper_overlay.set_subclass(sub)
    except Exception:
        pass
    # 격수 승급 복원 (2/3/4차).
    try:
        r = int(g("attacker_rank", 4))
    except Exception:
        r = 4
    if r not in (2, 3, 4):
        r = 4
    mw.attacker_rank = r
    try:
        mw.rb_rank2.blockSignals(True)
        mw.rb_rank3.blockSignals(True)
        mw.rb_rank4.blockSignals(True)
        if r == 2:
            mw.rb_rank2.setChecked(True)
        elif r == 3:
            mw.rb_rank3.setChecked(True)
        else:
            mw.rb_rank4.setChecked(True)
    finally:
        mw.rb_rank2.blockSignals(False)
        mw.rb_rank3.blockSignals(False)
        mw.rb_rank4.blockSignals(False)
    try:
        if mw._helper_overlay is not None:
            mw._helper_overlay.set_rank(r)
    except Exception:
        pass
    # ARM 은 UI 에서 숨겼고 "시작=ON" 기본 동작. 저장값 무시하고 항상 True
    # 로 복원해 시작 버튼 한 번으로 즉시 주입이 되게 한다.
    mw.chk_arm.blockSignals(True)
    mw.chk_arm.setChecked(True)
    mw.chk_arm.blockSignals(False)
    mw.chk_follow_only.setChecked(bool(g("follow_only", False)))
    # Param
    if g("conf") is not None: mw.conf_slider.setValue(int(g("conf")))
    if g("min_w") is not None: mw.minw_spin.setValue(int(g("min_w")))
    if g("min_h") is not None: mw.minh_spin.setValue(int(g("min_h")))
    if g("tol") is not None: mw.tol_spin.setValue(int(g("tol")))
    if g("yn") is not None: mw.yn_spin.setValue(int(g("yn")))
    # Network — 과거 str(list) 버그 복구 + list 방어. row UI 반영.
    pv = g("peers")
    if pv is not None:
        if isinstance(pv, str) and pv.startswith("["):
            import re as _re_ip
            ips = _re_ip.findall(r"\d+\.\d+\.\d+\.\d+", pv)
            if ips:
                pv = ips
        if hasattr(mw.net_dlg, "set_peers_string"):
            try:
                mw.net_dlg.set_peers_string(pv)
            except Exception:
                mw.peers_edit.setText(str(pv))
        else:
            if isinstance(pv, list):
                pv = ",".join(str(x) for x in pv)
            mw.peers_edit.setText(str(pv))
    if g("port") is not None: mw.port_spin.setValue(int(g("port")))
    if g("rate") is not None: mw.rate_spin.setValue(int(g("rate")))
    if g("healer_idx") is not None:
        try:
            mw.healer_idx_spin.setValue(int(g("healer_idx")))
            mw.cfg.net.healer_idx = int(g("healer_idx"))
        except Exception:
            pass
    # Cooldown region 복원.
    cx = g("cd_region_x"); cy = g("cd_region_y")
    cw = g("cd_region_w"); ch = g("cd_region_h")
    if cx is not None and cw is not None and int(cw) > 0 and int(cx) >= 0:
        mw.cfg.cooldown.region_x = int(cx)
        mw.cfg.cooldown.region_y = int(cy)
        mw.cfg.cooldown.region_w = int(cw)
        mw.cfg.cooldown.region_h = int(ch)
        mw.lbl_cd_region.setText(
            f"쿨 영역: ({cx},{cy}) {cw}×{ch}"
        )
    # Nickname region 복원.
    nx = g("nick_region_x"); ny = g("nick_region_y")
    nw = g("nick_region_w"); nh = g("nick_region_h")
    if nx is not None and nw is not None and int(nw) > 0 and int(nx) >= 0:
        mw.cfg.cooldown.nick_region_x = int(nx)
        mw.cfg.cooldown.nick_region_y = int(ny)
        mw.cfg.cooldown.nick_region_w = int(nw)
        mw.cfg.cooldown.nick_region_h = int(nh)
        if hasattr(mw, "lbl_nick_region"):
            mw.lbl_nick_region.setText(
                f"닉 영역: ({nx},{ny}) {nw}×{nh}"
            )
    # Buff region 복원.
    bx = g("buff_region_x"); by = g("buff_region_y")
    bw = g("buff_region_w"); bh = g("buff_region_h")
    if bx is not None and bw is not None and int(bw) > 0 and int(bx) >= 0:
        mw.cfg.cooldown.buff_region_x = int(bx)
        mw.cfg.cooldown.buff_region_y = int(by)
        mw.cfg.cooldown.buff_region_w = int(bw)
        mw.cfg.cooldown.buff_region_h = int(bh)
        if hasattr(mw, "lbl_buff_region"):
            mw.lbl_buff_region.setText(
                f"버프 영역: ({bx},{by}) {bw}×{bh}"
            )
    # HP/MP region 복원 (2026-04-20).
    for _k in ("hp", "mp"):
        _x = g(f"{_k}_region_x"); _y = g(f"{_k}_region_y")
        _w = g(f"{_k}_region_w"); _h = g(f"{_k}_region_h")
        if (_x is not None and _w is not None
                and int(_w) > 0 and int(_x) >= 0):
            setattr(mw.cfg.cooldown, f"{_k}_region_x", int(_x))
            setattr(mw.cfg.cooldown, f"{_k}_region_y", int(_y))
            setattr(mw.cfg.cooldown, f"{_k}_region_w", int(_w))
            setattr(mw.cfg.cooldown, f"{_k}_region_h", int(_h))
    # HP/MP 최대값 복원.
    try:
        v = g("hp_max")
        if v is not None and hasattr(mw, "hp_max_spin"):
            mw.hp_max_spin.blockSignals(True)
            mw.hp_max_spin.setValue(int(v))
            mw.hp_max_spin.blockSignals(False)
            mw.cfg.cooldown.hp_max = int(v)
    except Exception:
        pass
    try:
        v = g("mp_max")
        if v is not None and hasattr(mw, "mp_max_spin"):
            mw.mp_max_spin.blockSignals(True)
            mw.mp_max_spin.setValue(int(v))
            mw.mp_max_spin.blockSignals(False)
            mw.cfg.cooldown.mp_max = int(v)
    except Exception:
        pass
    # 공력증강 임계치 복원 — skill_dlg 가 만들어져 있어야 안전.
    try:
        v = g("gyoungryeok_mp_thr")
        if v is not None:
            mw.skill_dlg.gyoungryeok_mp_spin.setValue(int(v))
    except Exception:
        pass
    # F11 통합 실행 토글 복원 (Patch 2.12).
    try:
        v = g("f11_ab_combined")
        if v is not None and hasattr(
                mw.skill_dlg, "chk_f11_ab_combined"):
            mw.skill_dlg.chk_f11_ab_combined.setChecked(bool(v))
    except Exception:
        pass
    # 6개 추가 영역 복원.
    regs = g("regions") or {}
    if isinstance(regs, dict):
        for k, v in regs.items():
            if not isinstance(v, (list, tuple)) or len(v) != 4:
                continue
            try:
                x, y, w, h = [int(z) for z in v]
            except Exception:
                continue
            if w <= 0 or h <= 0:
                continue
            mw._regions[k] = (x, y, w, h)
            lb = mw._region_labels_kr.get(k, k)
            b = mw._region_buttons.get(k)
            if b is not None:
                b.setText(f"{lb} ✓")
    # 오버레이 수동 위치 + 편집 체크 복원.
    # ★ 순서 주의: overlay_on보다 먼저 복원해야 함. overlay_on이 True면
    # setChecked가 stateChanged → _on_toggle_overlay → _overlay_positions.get
    # 경로를 즉시 타므로, 이 시점에 dict가 비어 있으면 기본 좌표로 고정됨.
    positions_v = g("overlay_positions")
    if isinstance(positions_v, dict):
        for k, v in positions_v.items():
            if isinstance(v, (list, tuple)) and len(v) == 2:
                mw._overlay_positions[str(k)] = (int(v[0]), int(v[1]))
    edit_v = g("overlay_edit_on")
    if edit_v is not None and hasattr(mw, "chk_overlay_edit"):
        mw.chk_overlay_edit.setChecked(bool(edit_v))
    # region 시각화 토글 복원 (체크 시 stateChanged → overlay 생성+show).
    rov = g("region_overlay_on")
    if rov is not None and hasattr(mw, "chk_region_overlay"):
        mw.chk_region_overlay.setChecked(bool(rov))
    # Overlay 투명도 슬라이더 복원 — chk_overlay 토글보다 먼저.
    # 이유: _on_toggle_overlay 내부 생성 경로에서 슬라이더 값을 읽어 적용하므로
    # 여기서 값을 맞춰 두지 않으면 기본 90% 로 생성된 뒤 사용자 설정이 유실된다.
    op_v = g("overlay_opacity")
    if op_v is not None and hasattr(mw, "slider_overlay_opacity"):
        try:
            iv = int(op_v)
            if iv < 10:
                iv = 10
            elif iv > 100:
                iv = 100
            # blockSignals: 여기서 valueChanged → _save_settings 재호출 방지.
            mw.slider_overlay_opacity.blockSignals(True)
            mw.slider_overlay_opacity.setValue(iv)
            mw.slider_overlay_opacity.blockSignals(False)
            mw.lbl_overlay_opacity.setText(f"{iv}%")
        except Exception:
            pass
    # Overlay 토글: 저장값 무시, 항상 강제 ON 으로 시작.
    # 사용자 요청: UI 재시작 시 오버레이 자동 활성화 보장.
    # chk_overlay 기본값은 unchecked 이므로 setChecked(True) 는 반드시
    # stateChanged 를 발생시켜 _on_toggle_overlay 경로로 생성+show 된다.
    # 만약 현재 이미 True 면(이론상 없음) toggled 를 명시적으로 호출해 경로 보장.
    if mw.chk_overlay.isChecked():
        # 이미 체크됨 → signal 안 뜸. 수동으로 생성 경로 호출.
        try:
            mw._on_toggle_overlay(QtCore.Qt.Checked)
        except Exception:
            pass
    else:
        mw.chk_overlay.setChecked(True)
    # 스킬범위 오버레이 복원 (격수 전용 체크박스).
    # 타일 W/H 복원. 구 설정("skill_range_tile")은 W/H 둘 다 동일값 폴백.
    sr_tile = g("skill_range_tile")
    sr_tile_w = g("skill_range_tile_w")
    sr_tile_h = g("skill_range_tile_h")
    if sr_tile_w is None and sr_tile is not None:
        sr_tile_w = sr_tile
    if sr_tile_h is None and sr_tile is not None:
        sr_tile_h = sr_tile
    if sr_tile_w is not None and hasattr(mw, "spin_skill_tile_w"):
        try:
            iv = max(8, min(120, int(sr_tile_w)))
            mw.spin_skill_tile_w.blockSignals(True)
            mw.spin_skill_tile_w.setValue(iv)
            mw.spin_skill_tile_w.blockSignals(False)
        except Exception:
            pass
    if sr_tile_h is not None and hasattr(mw, "spin_skill_tile_h"):
        try:
            iv = max(8, min(120, int(sr_tile_h)))
            mw.spin_skill_tile_h.blockSignals(True)
            mw.spin_skill_tile_h.setValue(iv)
            mw.spin_skill_tile_h.blockSignals(False)
        except Exception:
            pass
    # 공용 Y오프셋은 제거됨 — 로드하지 않음. 이전 설정에 값이 있으면 무시.
    # 방향별 오프셋 8 개 (U/D/L/R 각각 X,Y).
    for _key, _attr in (
        ("skill_range_u_x", "spin_skill_u_x"),
        ("skill_range_u_y", "spin_skill_u_y"),
        ("skill_range_d_x", "spin_skill_d_x"),
        ("skill_range_d_y", "spin_skill_d_y"),
        ("skill_range_l_x", "spin_skill_l_x"),
        ("skill_range_l_y", "spin_skill_l_y"),
        ("skill_range_r_x", "spin_skill_r_x"),
        ("skill_range_r_y", "spin_skill_r_y"),
    ):
        _v = g(_key)
        if _v is not None and hasattr(mw, _attr):
            try:
                iv = max(-300, min(300, int(_v)))
                sp = getattr(mw, _attr)
                sp.blockSignals(True)
                sp.setValue(iv)
                sp.blockSignals(False)
            except Exception:
                pass
    # 스킬별 투명도 복원 (슬라이더 + 라벨).
    sr_alpha = g("skill_range_alpha")
    if isinstance(sr_alpha, dict) and hasattr(mw, "sld_skill_alpha"):
        for _nm, _sld in mw.sld_skill_alpha.items():
            if _nm in sr_alpha:
                try:
                    iv = int(sr_alpha[_nm])
                    iv = max(0, min(100, iv))
                    _sld.blockSignals(True)
                    _sld.setValue(iv)
                    _sld.blockSignals(False)
                    if _nm in mw.lbl_skill_alpha:
                        mw.lbl_skill_alpha[_nm].setText(f"{iv}%")
                except Exception:
                    pass
    # 스킬별 사용여부 복원.
    sr_en = g("skill_range_enabled")
    if isinstance(sr_en, dict) and hasattr(mw, "chk_skill_enabled"):
        for _nm, _chk in mw.chk_skill_enabled.items():
            if _nm in sr_en:
                try:
                    _chk.blockSignals(True)
                    _chk.setChecked(bool(sr_en[_nm]))
                    _chk.blockSignals(False)
                except Exception:
                    pass
    sr_on = g("skill_range_on")
    if sr_on is not None and hasattr(mw, "chk_skill_range"):
        try:
            mw.chk_skill_range.setChecked(bool(sr_on))
        except Exception:
            pass
    low_v = g("low_spec")
    if low_v is not None and hasattr(mw, "chk_low_spec"):
        mw.chk_low_spec.setChecked(bool(low_v))
    tab_v = g("tab_index")
    if tab_v is not None and hasattr(mw, "tabs"):
        try:
            mw.tabs.setCurrentIndex(int(tab_v))
        except Exception:
            pass
    # 닉네임 리스트 복원 (peers와 매칭).
    nicks_v = g("nicks")
    if nicks_v and hasattr(mw.net_dlg, "set_rows"):
        try:
            # peers 복원 직후라 net_dlg.get_peers()에 현재 IP들이 있음.
            ips = mw.net_dlg.get_peers() if hasattr(
                mw.net_dlg, "get_peers") else []
            if isinstance(nicks_v, list) and ips and len(nicks_v) == len(ips):
                mw.net_dlg.set_rows(nicks_v, ips)
        except Exception:
            pass
    # 기원 택1 — 봉황이 기본. shinryoung이 True로 저장되었을 때만 변경.
    if g("rb_shinryoung"):
        mw.skill_dlg.rb_shinryoung.setChecked(True)
    else:
        mw.skill_dlg.rb_bonghwang.setChecked(True)
    if g("spin_bonghwang") is not None:
        mw.skill_dlg.spin_bonghwang.setValue(int(g("spin_bonghwang")))
    if g("spin_shinryoung") is not None:
        mw.skill_dlg.spin_shinryoung.setValue(int(g("spin_shinryoung")))
    mw.skill_dlg.chk_honmasul.setChecked(bool(g("chk_honmasul", True)))
    if g("spin_honmasul") is not None:
        mw.skill_dlg.spin_honmasul.setValue(int(g("spin_honmasul")))
    # Skill 조건부
    chks = g("skill_chks") or {}
    for n, v in chks.items():
        if n in mw.skill_chks:
            mw.skill_chks[n].setChecked(bool(v))
    spins = g("skill_spins") or {}
    for n, v in spins.items():
        if n in mw.skill_spins:
            try:
                mw.skill_spins[n].setValue(int(v))
            except Exception:
                pass
    if g("parlyuk_offset") is not None:
        mw.parlyuk_spin.setValue(int(g("parlyuk_offset")))
    if g("parlyuk_maps") is not None:
        mw.parlyuk_maps_edit.setText(str(g("parlyuk_maps")))
    # 쩔캐 설정 복원 (2026-06-12).
    try:
        if hasattr(mw, "chk_hyeonin"):
            mw.chk_hyeonin.setChecked(bool(g("jipok_hyeonin", False)))
        if g("spin_jipok_gyoung") is not None and hasattr(
                mw, "spin_jipok_gyoung"):
            mw.spin_jipok_gyoung.setValue(int(g("spin_jipok_gyoung")))
        if g("spin_jipok_jipok") is not None and hasattr(
                mw, "spin_jipok_jipok"):
            mw.spin_jipok_jipok.setValue(int(g("spin_jipok_jipok")))
        if g("jipok_maps") is not None and hasattr(mw, "jipok_maps_edit"):
            mw.jipok_maps_edit.setText(str(g("jipok_maps")))
    except Exception:
        pass
    # Window geometry 복원 — 화면 밖이면 무시.
    try:
        wx = g("win_x"); wy = g("win_y")
        ww = g("win_w"); wh = g("win_h")
        if wx is not None and wy is not None and ww and wh:
            wx, wy, ww, wh = int(wx), int(wy), int(ww), int(wh)
            vg = QtWidgets.QApplication.primaryScreen().availableGeometry()
            if (vg.left() - 50 <= wx <= vg.right() - 100
                    and vg.top() - 50 <= wy <= vg.bottom() - 100
                    and 300 <= ww <= 4000 and 200 <= wh <= 3000):
                mw.setGeometry(wx, wy, ww, wh)
    except Exception:
        pass


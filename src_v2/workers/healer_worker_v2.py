"""Healer Worker V2 — wires up Eyes + Brain + Hands + Muscle + Memory + UI.

This is the entry point that replaces src/workers/healer_worker.py.

External adapters (grabber, ocr, yolo, hpmp, cooldown, udp, key) are injected
at construction. In production, real implementations come from src/* modules.
In tests, mocks substitute.

Design ref: §11.2 healer_worker.py port + §3.1 data flow
"""
from __future__ import annotations
import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore
from ..core.plugin_registry import PluginRegistry

from ..eyes.capture import CaptureWatcher
from ..eyes.yolo_watcher import YoloWatcher
from ..eyes.ocr_watcher import OcrWatcher
from ..eyes.cooldown_watcher import CooldownWatcher
from ..eyes.hpmp_watcher import HpMpWatcher
from ..eyes.xp_watcher import XpWatcher
from ..eyes.udp_watcher import UdpWatcher
from ..eyes.cooldown_uplink import CooldownUplink
from ..eyes.tab_confirm_driver import TabConfirmState, tab_confirm_tick

from ..hands.input_dispatcher import InputDispatcher
from ..hands.numlock_cycle import NumlockCycler
from ..hands.skill_executor import SkillExecutor, HandsAPI
from ..hands import sequences as _seq_pkg  # noqa: F401  ensure registered

from ..brain import rules as _rules_pkg  # noqa: F401  ensure registered
from ..brain.rule_engine import RuleEngine
from ..brain.decision import RuleContextBuilder
from ..brain.follower import make_follower
from ..brain.integration_tick import (
    IntegrationState, integration_tick, arm_tab_lock_pending,
)

from ..muscle.main_loop import MainLoop

from ..memory.action_log import ActionLog
from ..memory.ai_hook import NullAiHook
from ..memory.outcome_verifier import OutcomeVerifier
from ..memory.anomaly_detector import AnomalyDetector
from ..memory.self_healing import SelfHealingLoop
from ..brain.recovery import RecoveryDispatcher

from ..learning import declare_learnables, MetaLearnerRunner

from ..ui.publisher import UiPublisher

log = logging.getLogger("src_v2.workers.healer")


@dataclass
class HealerConfig:
    """Tuning knobs and feature flags. Builds RuleContext.cfg."""
    # Eyes poll intervals
    capture_poll_sec: float = 0.02
    yolo_poll_sec: float = 0.05
    ocr_poll_sec: float = 0.33   # 3Hz — 좌표/맵은 자주 안 바뀜. 과거 고빈도 폴링은 OCR 낭비
    cooldown_poll_sec: float = 1.0
    buff_poll_sec: float = 1.0
    hpmp_poll_sec: float = 0.33  # 3Hz — 자힐/공증 트리거 우선, 0.5s에서 상향
    xp_poll_sec: float = 2.0
    udp_poll_sec: float = 0.02
    # Muscle
    main_hz_cap: int = 200
    combat_band: int = 2
    # Numlock
    numlock_enabled: bool = False
    numlock_interval_sec: float = 30.0
    # UI
    ui_publish_hz: int = 15
    # Memory
    action_log_capacity: int = 4096
    action_log_file: Optional[str] = None
    # Self-evolving (Phase 7)
    learning_enabled: bool = False
    learning_poll_sec: float = 300.0
    learning_min_score: float = 0.5
    learning_rollback_sec: float = 300.0
    learning_regression_factor: float = 1.1
    # AlphaGo (Phase 8) — disabled by default until enough action_log accumulated
    alphago_enabled: bool = False
    alphago_min_records: int = 1000
    alphago_poll_sec: float = 1800.0
    alphago_min_confidence: float = 0.7
    alphago_train_steps: int = 100
    alphago_batch_size: int = 64
    alphago_self_play_episodes: int = 50
    alphago_improve_factor: float = 1.05
    alphago_mcts_sims: int = 16
    alphago_mcts_depth: int = 3
    alphago_weights_path: Optional[str] = None
    # Error detection / recovery / self-healing (2026-04-25)
    outcome_verifier_enabled: bool = True
    outcome_verifier_poll_sec: float = 0.05
    recovery_enabled: bool = True
    anomaly_enabled: bool = True
    anomaly_sample_sec: float = 1.0
    anomaly_short_sec: float = 60.0
    anomaly_long_sec: float = 1800.0
    anomaly_z_threshold: float = 2.0
    self_healing_enabled: bool = False  # 운영 안정 후 활성
    self_healing_poll_sec: float = 300.0
    self_healing_evolution_log: Optional[str] = None
    # Rule cfg (passed into RuleContext)
    rule_cfg: Dict[str, Any] = field(default_factory=lambda: {
        "self_heal_hp_thr": 50,
        "self_heal_burst_count": 5,
        "self_heal_burst_gap_ms": 100,
        "self_heal_enable_block_b": True,
        "gyoungryeok_mp_thr": 30,
        "gyoungryeok_enabled": True,
        "baekho_enabled": True,
        "parlyuk_enabled": True,
        "parlyuk_offset_sec": 0,
        "parhon_enabled": True,
        "parhon_edge_sec": 3,
        "mujang_enabled": True,
        "boho_enabled": True,
        # 2026-05-05 P0-2 fix: 금강불체 manual-only. 자동 fire 없음
        # (run_geumgang_test 수동 호출 경로만). 일관성 위해 키만 추가.
        "geumgang_enabled": False,
        "seq_rclick_enabled": True,
        "seq_rclick_duration_ms": 1500,
        "seq_rclick_interval_ms": 500,
        "tab_lock_enabled": True,
        "combat_band": 1,
        "_map_transition_in_progress": False,
    })


class HealerWorkerV2:
    """Composition root. Build with adapters, call start()/stop().

    Adapters are passed in __init__ — in tests, pass mocks.
    """

    def __init__(self,
                 cfg: Optional[HealerConfig] = None,
                 # eye adapters
                 grabber: Any = None,
                 yolo: Any = None,
                 ocr: Any = None,
                 cooldown: Any = None,
                 buff: Any = None,
                 chat: Any = None,
                 hpmp: Any = None,
                 xp: Any = None,
                 udp: Any = None,
                 # hand adapters
                 keys: Any = None,
                 numlock_adapter: Any = None,
                 # uplink (cooldown UDP 역송)
                 uplink_sender: Any = None,
                 # ui
                 emit_callback: Optional[Callable[[dict], None]] = None,
                 # 진단 로그 콜백 (facade 의 _emit_log → log_msg signal). v1
                 # healer_worker.py 의 self.log.info 와 동치 라우팅.
                 log_callback: Optional[Callable[[str], None]] = None,
                 ai_hook: Any = None,
                 ) -> None:
        self.cfg = cfg or HealerConfig()
        self.bus = EventBus()
        self.store = SnapshotStore()
        # 진단 로그 콜백 — set_log 못 받는 모든 v2 sub-component 에서 사용.
        # facade 가 제공하면 GUI log_msg 로 흐르고 facade file logger 에도 적힘.
        # 미지정 시 python logger 로 폴백.
        self._log_emit: Callable[[str], None] = (
            log_callback if callable(log_callback) else (lambda s: log.info(s))
        )

        # ----- Hands -----
        self.dispatcher = InputDispatcher(keys=keys)
        # v1 1:1 NumLockCycler: slots(NumPad VK) 토글 ON 으로 잠금 → 게임 자동 시전.
        # adapter / interval_sec 은 v2 호환 (사용 안함). slots 는 set_primary_vks
        # 로 갱신, default 1/2/3.
        self.cycler = NumlockCycler(
            slots=[0x61, 0x62],
            poll_ms=200,
            start_delay_sec=1.0,
        )
        # v1 동일: startup 's' 키 1회 송신 플래그 + 격수창 fg 감지 스레드 사용.
        self._startup_s_sent: bool = False
        self._keys_adapter = keys
        self._numlock_adapter = numlock_adapter
        self._hand_q: queue.PriorityQueue = queue.PriorityQueue()
        # v1 1:1: skill_scheduler.request_cast(skill_scheduler.py:111-112) 와
        # 동일하게 dedup 활성. 동일 name 중복 요청 무시 → udp 매 frame publish
        # 시 폭주 방지.
        self.hands_api = HandsAPI(self._hand_q, self.dispatcher, dedup=True)
        # 2026-04-28 audit 8.1 2단계: worker_state + ctx_builder.extras 단일화.
        # DecisionScratch.data 가 단일 dict ref. 두 외부 namespace 가 같은 메모리
        # 공유 → 룰 ↔ 시퀀스 통신 누락 차단, 디버깅 시 단일 source.
        from ..brain.decision_scratch import DecisionScratch
        self._scratch = DecisionScratch()
        self._worker_state: Dict[str, Any] = self._scratch.data
        self.executor = SkillExecutor(
            self._hand_q, self.bus, self.dispatcher,
            worker_state=self._worker_state,
            cycler=self.cycler,
            hpmp_adapter=hpmp,
            chat_adapter=chat,
            hands_api=self.hands_api,
            log_callback=self._log_emit,
        )
        self.executor.set_ready_gate(self._executor_ready_gate)
        self.executor.set_ctx_provider(self._executor_ctx_provider)

        # ----- Brain -----
        # ctx_builder.extras = scratch.data (같은 ref).
        self.ctx_builder = RuleContextBuilder(
            cfg=self.cfg.rule_cfg,
            in_progress=self.executor.in_progress,
            extras=self._scratch.data,
        )
        # 호환 keys — _worker_state self-ref (legacy 룰 / 시퀀스 호환).
        self._scratch.data["_worker_state"] = self._scratch.data
        self.rule_engine = RuleEngine(
            self.store, self.bus, self.hands_api, ctx_builder=self.ctx_builder,
            log_callback=self._log_emit,
        )

        # ----- Eyes -----
        self.capture = CaptureWatcher(
            self.store, self.bus, grabber=grabber,
            poll_sec=self.cfg.capture_poll_sec,
        )
        self.yolo = YoloWatcher(
            self.store, self.bus, yolo=yolo,
            poll_sec=self.cfg.yolo_poll_sec,
            log_callback=self._log_emit,
        )
        self.ocr = OcrWatcher(
            self.store, self.bus, ocr=ocr,
            poll_sec=self.cfg.ocr_poll_sec,
            log_callback=self._log_emit,
        )
        self.cooldown = CooldownWatcher(
            self.store, self.bus, adapter=cooldown, slot="cd",
            poll_sec=self.cfg.cooldown_poll_sec,
            log_callback=self._log_emit,
        )
        self.buff = CooldownWatcher(
            self.store, self.bus, adapter=buff, slot="buff",
            poll_sec=self.cfg.buff_poll_sec,
            log_callback=self._log_emit,
        )
        self.hpmp = HpMpWatcher(
            self.store, self.bus, adapter=hpmp,
            poll_sec=self.cfg.hpmp_poll_sec,
            log_callback=self._log_emit,
        )
        self.xp = XpWatcher(
            self.store, self.bus, adapter=xp,
            poll_sec=self.cfg.xp_poll_sec,
        )
        self.udp = UdpWatcher(
            self.store, self.bus, adapter=udp,
            poll_sec=self.cfg.udp_poll_sec,
            log_callback=self._log_emit,
        )

        # ----- v1 통합 분기 (Follower + tab_confirm + integration tick) -----
        # Follower 는 v1 Follower (controller.py:Follower) 직접 사용 — 1:1 검증된 모듈.
        # v1 1:1: muscle 보다 먼저 생성 (decide_direction 의 B1/B2 trail follow 가
        # follower.next_waypoint / exit_dir / direction / last_seen_in 호출).
        self.follower = make_follower()

        # ----- Muscle -----
        # 2026-04-27 audit 5.1: rule_cfg ref 직접 전달. integration_tick 가
        # rule_cfg["coord_tol"]=1 갱신하면 muscle 즉시 반영. combat_band 도
        # rule_cfg 에 미리 박음 (이전엔 별도 dict 라 변경 영원 무시).
        try:
            self.cfg.rule_cfg.setdefault("combat_band", self.cfg.combat_band)
        except Exception:
            pass
        self.muscle = MainLoop(
            self.store, self.dispatcher, self.cycler,
            cfg=self.cfg.rule_cfg,
            hz_cap=self.cfg.main_hz_cap,
            follower=self.follower,
        )
        self.integ_state = IntegrationState()
        self.tab_confirm_state = TabConfirmState()
        # integration tick thread (5Hz) — TAB-CONFIRM, F1-PEND, edge, parlyuk-tol
        self._integ_stop_evt = threading.Event()
        self._integ_thread: Optional[threading.Thread] = None
        self._integ_period_sec: float = 0.2  # 5Hz

        # ----- Cooldown UDP 역송 (1Hz) -----
        self.uplink: Optional[CooldownUplink] = None
        if uplink_sender is not None:
            self.uplink = CooldownUplink(
                self.store, uplink_sender,
                period_sec=1.0,
                alert_seq_provider=lambda: self.integ_state.alert_seq,
            )

        # ----- Memory -----
        self.action_log = ActionLog(
            self.store, self.bus,
            capacity=self.cfg.action_log_capacity,
            file_path=self.cfg.action_log_file,
        )
        self.ai_hook = ai_hook or NullAiHook()

        # ----- Error detection / recovery (2026-04-25) -----
        self.outcome_verifier: Optional[OutcomeVerifier] = None
        if self.cfg.outcome_verifier_enabled:
            self.outcome_verifier = OutcomeVerifier(
                self.store, self.bus,
                poll_sec=self.cfg.outcome_verifier_poll_sec,
                enabled=True,
            )
        self.recovery: Optional[RecoveryDispatcher] = None
        if self.cfg.recovery_enabled:
            self.recovery = RecoveryDispatcher(
                self.bus, self.hands_api,
                keys_adapter=keys,
                worker_state=self._worker_state,
                log_emit=self._log_emit,
                enabled=True,
                store=self.store,
            )
        self.anomaly: Optional[AnomalyDetector] = None
        if self.cfg.anomaly_enabled:
            self.anomaly = AnomalyDetector(
                self.store, self.action_log, self.bus,
                sample_sec=self.cfg.anomaly_sample_sec,
                short_window_sec=self.cfg.anomaly_short_sec,
                long_window_sec=self.cfg.anomaly_long_sec,
                z_threshold=self.cfg.anomaly_z_threshold,
                enabled=True,
            )
        self.self_healing: Optional[SelfHealingLoop] = None
        if self.cfg.self_healing_enabled:
            self.self_healing = SelfHealingLoop(
                self.action_log,
                poll_sec=self.cfg.self_healing_poll_sec,
                evolution_log_path=self.cfg.self_healing_evolution_log,
                enabled=True,
            )

        # ----- Learning (Phase 7) -----
        self.learner: Optional[MetaLearnerRunner] = None
        if self.cfg.learning_enabled:
            declare_learnables()
            self.learner = MetaLearnerRunner(
                self.action_log,
                poll_sec=self.cfg.learning_poll_sec,
                min_score_threshold=self.cfg.learning_min_score,
                rollback_window_sec=self.cfg.learning_rollback_sec,
                regression_factor=self.cfg.learning_regression_factor,
            )

        # ----- AlphaGo (Phase 8) -----
        self.alphago_runner = None
        self.alphago_coach = None
        if self.cfg.alphago_enabled:
            from ..learning.alphago import (
                PolicyNet, ValueNet, EnvModel, ReplayBuffer, Trainer,
                AlphaGoRunner, register_neural_advisor, Coach,
            )
            policy = PolicyNet()
            value = ValueNet()
            env = EnvModel(n_clusters=16)
            buffer = ReplayBuffer(capacity=50000)
            trainer = Trainer(policy, value, lr=1e-3)
            self.alphago_runner = AlphaGoRunner(
                policy, value, env,
                enabled=True,
                min_confidence=self.cfg.alphago_min_confidence,
                mcts_sims=self.cfg.alphago_mcts_sims,
                mcts_depth=self.cfg.alphago_mcts_depth,
            )
            # Inject runner into RuleContext extras
            self.ctx_builder.extras["alphago_runner"] = self.alphago_runner
            # Add nn_min_confidence to cfg
            self.cfg.rule_cfg.setdefault("nn_min_confidence", self.cfg.alphago_min_confidence)
            # Register neural rule
            register_neural_advisor()
            # Coach
            self.alphago_coach = Coach(
                action_log_provider=self.action_log.all,
                replay_buffer=buffer,
                env_model=env,
                policy_net=policy,
                value_net=value,
                trainer=trainer,
                poll_sec=self.cfg.alphago_poll_sec,
                min_records=self.cfg.alphago_min_records,
                train_steps=self.cfg.alphago_train_steps,
                batch_size=self.cfg.alphago_batch_size,
                self_play_episodes=self.cfg.alphago_self_play_episodes,
                improve_factor=self.cfg.alphago_improve_factor,
            )

        # ----- UI -----
        self.ui_publisher: Optional[UiPublisher] = None
        if emit_callback:
            self.ui_publisher = UiPublisher(
                self.store, emit_callback,
                hz=self.cfg.ui_publish_hz,
                watchers={
                    "capture": self.capture,
                    "yolo": self.yolo,
                    "ocr": self.ocr,
                },
            )

        self._running = False

    def start(self) -> None:
        if self._running:
            return
        log.info("HealerWorkerV2 start")
        # 패치 버전 스탬프 — 로그 보고 판단 착오 방지 (2026-05-02)
        from ..adapters.yolo_adapter import RealYoloAdapter
        from ..eyes.yolo_watcher import YoloWatcher
        from ..eyes.tab_confirm_driver import RED_QUIET_MS as _RED_QUIET_MS
        log.info(
            "[PATCH] 2026-05-02 yolo_stale_gate+white_cache+red_quiet | "
            "stale_ms=%.0f fresh_ms=%.0f poll_sec=%.2f "
            "white_cache_ttl_ms=%.0f red_quiet_ms=%.0f",
            RealYoloAdapter.STALE_MS, RealYoloAdapter.FRESH_MS,
            YoloWatcher.DEFAULT_POLL_SEC, YoloWatcher.WHITE_CACHE_TTL_MS,
            _RED_QUIET_MS,
        )
        # Memory subscribes first
        self.action_log.attach()
        if self.outcome_verifier:
            self.outcome_verifier.attach()
            self.outcome_verifier.start()
        if self.recovery:
            self.recovery.attach()
        if self.anomaly:
            self.anomaly.start()
        if self.self_healing:
            self.self_healing.start()
        # Brain (rule engine) loads rules + subscribes
        self.rule_engine.start()
        # Hands executor starts
        self.executor.start()
        # NumLockCycler — v1 1:1: thread 시작. set_armed(True) 로 lock 시작.
        try:
            # set_log 는 facade _emit_log 로 라우팅 → GUI log_msg + facade file
            # logger 둘 다에 [CYCLE] lock vk= ... 가 보임.
            self.cycler.set_log(self._log_emit)
            # set_lock_debug 도 같이 — [LOCK-TRACE] 진단 라인.
            try:
                from ..hands.numlock_cycle import set_lock_debug
                set_lock_debug(self._log_emit)
            except Exception:
                pass
            primary = list(self._worker_state.get("primary_vks") or [0x61, 0x62])
            self.cycler.set_slots(primary)
            self.cycler.start()
            self.cycler.set_armed(True)
            self._log_emit(
                f"[CYCLE-START] slots={[hex(v) for v in primary]} armed=True "
                f"start_delay=1.0s"
            )
        except Exception:
            log.exception("cycler start fail")
        # SkillExecutor 와 NumLockCycler 가 ready_gate 로 sync 되도록 worker_state
        # 에 cycler 참조 + ready 함수 노출.
        self._worker_state["_cycler"] = self.cycler
        self._worker_state["_cycler_ready"] = (
            lambda: bool(getattr(self.cycler, "is_initial_lock_done", lambda: True)())
        )
        # Eyes start
        for w in (self.capture, self.yolo, self.ocr, self.cooldown,
                  self.buff, self.hpmp, self.xp, self.udp):
            w.start()
        # UI publisher
        if self.ui_publisher:
            self.ui_publisher.start()
        # Learning thread (after action_log is attached and watchers running)
        if self.learner:
            self.learner.start()
        # AlphaGo coach (daemon thread)
        if self.alphago_coach:
            self.alphago_coach.start()
        # Muscle last
        self.muscle.start()
        # v1 통합 tick (5Hz) — Follower update + tab_confirm + edge detection.
        self._integ_stop_evt.clear()
        self._integ_thread = threading.Thread(
            target=self._integ_loop, name="integ_tick", daemon=True,
        )
        self._integ_thread.start()
        # Cooldown UDP 역송 (1Hz)
        if self.uplink:
            self.uplink.start()
        # v1 동일: 워커 시작 후 옛바창 fg 확보되면 's' 키 1회 송신.
        # 사용자 지시 2026-04-21 (healer_worker.py:1785-1795).
        self._start_startup_s_thread()
        # 2026-04-27 audit 8.1 4단계: 계약 로그 — 워커 시작 시점 cfg 진단.
        # 사용자가 "체크 안 한 스킬도 시전" 또는 "체크했는데 안 시전" 신고 시
        # 이 로그 한 줄로 cfg 실측치 확인.
        try:
            _enabled_keys = (
                "baekho_enabled", "parlyuk_enabled", "parhon_enabled",
                "gyoungryeok_enabled", "mujang_enabled", "boho_enabled",
                "self_heal_enabled", "self_revive_enabled",
                # 2026-05-05 P0-2: geumgang_enabled 독립 키 추가 (manual-only).
                "geumgang_enabled",
            )
            _cfg_summary = {k: bool(self.cfg.rule_cfg.get(k, True)) for k in _enabled_keys}
            self._log_emit(f"[CFG-CONTRACT] enabled={_cfg_summary}")
            self._log_emit(
                f"[CFG-CONTRACT] thresholds "
                f"self_heal_hp_thr={self.cfg.rule_cfg.get('self_heal_hp_thr')} "
                f"gyoungryeok_mp_thr={self.cfg.rule_cfg.get('gyoungryeok_mp_thr')} "
                f"parlyuk_offset_sec={self.cfg.rule_cfg.get('parlyuk_offset_sec')}"
            )
        except Exception:
            pass
        self._running = True

    def _start_startup_s_thread(self) -> None:
        """fg 감지 후 's' 키 1회 송신. v1 healer_worker.py 의 _startup_s 동작."""
        import threading as _t
        import time as _time

        emit = self._log_emit  # facade 라우팅

        def _runner():
            try:
                _send_input = None
                # adapter 에 send_vk 가 있으면 그것을, 아니면 src.input.keys 사용.
                ad = self._keys_adapter
                if ad is not None and hasattr(ad, "send_vk"):
                    def _send_input(vk, up):
                        try:
                            ad.send_vk(int(vk), bool(up))
                        except Exception:
                            pass
                if _send_input is None:
                    try:
                        from src.input.keys import _send_input as _v1_send  # type: ignore
                        _send_input = _v1_send
                    except Exception:
                        emit("[STARTUP-S] _send_input import 실패 — 송신 불가")
                        return
                # fg 감지: keys adapter 의 hwnd 가 있으면 _is_fg_hwnd 로 검사.
                hwnd = getattr(ad, "hwnd", None) if ad is not None else None
                try:
                    from src.utils.win_helpers import _is_fg_hwnd  # type: ignore
                except Exception:
                    _is_fg_hwnd = None  # noqa: N806
                emit(
                    f"[STARTUP-S] thread 시작 — fg 감지 polling "
                    f"hwnd={hwnd} fg_check={'on' if _is_fg_hwnd else 'off'}"
                )
                # 최대 30초 동안 fg 가 잡힐 때까지 0.2s 폴링.
                t_end = _time.time() + 30.0
                while _time.time() < t_end and not self._startup_s_sent:
                    fg_ok = True
                    if _is_fg_hwnd is not None and hwnd:
                        try:
                            fg_ok = bool(_is_fg_hwnd(hwnd))
                        except Exception:
                            fg_ok = True
                    if fg_ok:
                        try:
                            _send_input(0x53, up=False)  # 'S' down
                            _time.sleep(0.05)
                            _send_input(0x53, up=True)
                            emit("[STARTUP-S] 's' 키 송신 완료")
                        except Exception as e:  # noqa: BLE001
                            emit(f"[STARTUP-S] 실패: {e}")
                        self._startup_s_sent = True
                        return
                    _time.sleep(0.2)
                if not self._startup_s_sent:
                    emit("[STARTUP-S] timeout 30s — fg 못 잡음, 송신 skip")
            except Exception as e:  # noqa: BLE001
                emit(f"[STARTUP-S] thread err: {e}")

        try:
            t = _t.Thread(target=_runner, name="startup_s", daemon=True)
            t.start()
        except Exception:
            log.exception("startup_s thread spawn fail")

    def _integ_loop(self) -> None:
        """v1 healer_worker.run() 통합 tick — 5Hz.

        매 tick:
          1. Follower.update(snap.attacker_state) → exit_dir/direction 갱신
          2. tab_confirm_tick(red_raw, white_raw, h_coord)
          3. integration_tick(...) → snap 의 v1 신호 필드 갱신
          4. muscle.DecisionState 의 force_exit/f1_pend_active sync.
        """
        import time as _t
        from ..brain.follower import adapt_state
        log.info("integ_tick start period=%.2fs", self._integ_period_sec)
        while not self._integ_stop_evt.wait(self._integ_period_sec):
            try:
                snap = self.store.read()
                # 1) Follower.update — v1 atk State 어댑터 통과
                try:
                    v1_state = adapt_state(getattr(snap, "attacker_state", None))
                    self.follower.update(v1_state)
                except Exception:
                    log.exception("follower.update fail")
                # 2) TAB-CONFIRM Route A — 흰탭 streak + ARM gate + 키 송신
                try:
                    tab_confirm_tick(
                        self.store, self.tab_confirm_state, self.follower,
                        log_emit=self._log_emit,
                    )
                except Exception:
                    log.exception("tab_confirm_tick fail")
                # 3) 통합 tick (parlyuk-tol, edge, post-heal-tab, tab_lock_pending)
                try:
                    integration_tick(
                        store=self.store,
                        state=self.integ_state,
                        follower=self.follower,
                        rule_cfg=self.cfg.rule_cfg,
                        ctx_extras=self.ctx_builder.extras,
                        request_cast=self._request_cast_by_name,
                        worker_state=self._worker_state,
                    )
                except Exception:
                    log.exception("integration_tick fail")
                # 4) muscle DecisionState sync — force_exit / f1_pend_active
                try:
                    ds = self.muscle._dec_state
                    snap2 = self.store.read()
                    if snap2.force_exit_active:
                        ds.force_exit_until = _t.time() + max(
                            0.0, snap2.force_exit_remaining,
                        )
                        ds.force_exit_dir = snap2.force_exit_dir
                    else:
                        ds.force_exit_until = 0.0
                    ds.f1_pend_active = bool(snap2.f1_pend_active)
                    ds.last_map_change_ts = self.integ_state._last_map_change_ts
                except Exception:
                    pass
                # 5) attacker_map_seq edge 감지 → TAB-LOCK arm
                try:
                    seq = int(getattr(snap, "attacker_map_seq", 0) or 0)
                    if seq != self.tab_confirm_state._last_attacker_map_seq:
                        # tab_confirm_driver 도 동일 edge 감지 — 여기선 추가로 TAB-LOCK arm.
                        arm_tab_lock_pending(self.integ_state)
                except Exception:
                    pass
            except Exception:
                log.exception("integ_loop iter fail")
        log.info("integ_tick stop")

    def _request_cast_by_name(self, name: str) -> None:
        """integration_tick 가 사용하는 helper — name 으로 cast 요청.

        rule 우회. 직접 sequence 큐에 push.
        """
        from ..core.types import CastRequest
        # name → priority (v1 우선순위 추정: revive=10, parhon=15, 무장/보호=40, tab_lock=50).
        prio_map = {
            "자가부활": 5, "self_revive": 5,
            "격수부활": 10, "attacker_revive": 10,
            "self_heal": 10,
            "파혼술": 15, "parhon": 15,
            "공력증강": 20, "gyoungryeok": 20,
            "백호의희원": 25, "백호의희원첨": 25, "baekho": 25,
            "파력무참": 30, "parlyuk": 30,
            "무장": 40, "mujang": 40,
            "보호": 40, "boho": 40,
            "금강불체": 45, "geumgang": 45,
            "tab_lock": 50,
        }
        # name → registered sequence name 매핑 (한국어 이름 → snake_case)
        name_alias = {
            "자가부활": "self_revive",
            "격수부활": "attacker_revive",
            "파혼술": "parhon",
            "공력증강": "gyoungryeok",
            "백호의희원": "baekho",
            "백호의희원첨": "baekho",
            "파력무참": "parlyuk",
            "무장": "mujang",
            "보호": "boho",
            "금강불체": "geumgang",
        }
        seq_name = name_alias.get(name, name)
        prio = prio_map.get(name, 50)
        req = CastRequest(name=seq_name, priority=prio)
        try:
            self.hands_api.request_cast(req)
        except Exception:
            log.exception("request_cast_by_name fail name=%s seq=%s", name, seq_name)

    # ---------------------------------------------------------------- #
    # SkillExecutor wiring helpers (set_ready_gate / set_ctx_provider)
    # ---------------------------------------------------------------- #
    def _executor_ready_gate(self) -> bool:
        """v1 SkillScheduler ready_gate — cycler 초기 lock 완료 후 시전 허용.

        worker_state["_cycler_ready"] 가 callable 로 박혀 있으면 그 결과 사용.
        cycler 미설정/예외 시 True (시전 유예 안 함).
        """
        try:
            fn = self._worker_state.get("_cycler_ready") if hasattr(self, "_worker_state") else None
            if callable(fn):
                return bool(fn())
            # cycler 직접 조회 fallback.
            cyc = getattr(self, "cycler", None)
            if cyc is not None:
                _ok = getattr(cyc, "is_initial_lock_done", None)
                if callable(_ok):
                    return bool(_ok())
        except Exception:
            pass
        return True

    def _executor_ctx_provider(self) -> dict:
        """v1 SkillScheduler ctx_provider — verify 시 cooldowns/buffs 풀 제공.

        snap 의 cooldown_reading.skills 를 cooldowns 로, buff_*_active 를
        buffs 로 노출. verify_kind 룰이 풀 조회 시 사용.
        """
        try:
            snap = self.store.read()
        except Exception:
            return {"cooldowns": {}, "buffs": {}}
        cd_pool: dict = {}
        try:
            cr = getattr(snap, "cooldown_reading", None)
            if cr is not None:
                sk = getattr(cr, "skills", None) or {}
                cd_pool = dict(sk)
        except Exception:
            pass
        buff_pool: dict = {
            "백호의희원": int(bool(getattr(snap, "buff_baekho_active", False))),
            "파력무참": int(bool(getattr(snap, "buff_parlyuk_active", False))),
            "공력증강": int(bool(getattr(snap, "buff_gyoungryeok_active", False))),
        }
        return {"cooldowns": cd_pool, "buffs": buff_pool}

    def stop(self, timeout: float = 3.0) -> None:
        if not self._running:
            return
        log.info("HealerWorkerV2 stop")
        # integ thread 먼저 중단 (snap update 가 이후 단계에서 영향 없게).
        self._integ_stop_evt.set()
        if self._integ_thread and self._integ_thread.is_alive():
            try:
                self._integ_thread.join(timeout=timeout)
            except Exception:
                pass
        if self.uplink:
            try:
                self.uplink.stop(timeout=timeout)
            except Exception:
                pass
        # Reverse order
        self.muscle.stop(timeout=timeout)
        if self.alphago_coach:
            self.alphago_coach.stop(timeout=timeout)
        if self.learner:
            self.learner.stop(timeout=timeout)
        if self.ui_publisher:
            self.ui_publisher.stop(timeout=timeout)
        for w in (self.udp, self.xp, self.hpmp, self.buff, self.cooldown,
                  self.ocr, self.yolo, self.capture):
            w.stop(timeout=timeout)
        self.executor.stop(timeout=timeout)
        # NumLockCycler 종료 — v1 동일: _unlock_all + ensure_numlock_off 자동 수행.
        try:
            if hasattr(self.cycler, "stop"):
                self.cycler.stop()
            if hasattr(self.cycler, "join"):
                self.cycler.join(timeout=2.0)
        except Exception:
            log.exception("cycler stop fail")
        self.dispatcher.release_all()
        # Error detection / recovery shutdown
        if self.self_healing:
            try:
                self.self_healing.stop(timeout=timeout)
            except Exception:
                pass
        if self.anomaly:
            try:
                self.anomaly.stop(timeout=timeout)
            except Exception:
                pass
        if self.outcome_verifier:
            try:
                self.outcome_verifier.stop(timeout=timeout)
            except Exception:
                pass
        self.action_log.close()
        self._running = False

    # ---------------------------------------------------------------------
    # Region setters — V2MainWindow 가 직접 호출 (facade 우회).
    # Adapter 에 set_region/set_hp_region/... 가 있으면 위임. 없으면 no-op.
    # 영역은 adapter level 에 저장되며 watcher 가 다음 read() 사이클부터 적용.
    # ---------------------------------------------------------------------
    def _adapter_for(self, key: str):
        # base_watcher 모든 watcher 는 self.adapter 보유. capture 는 self.grabber.
        if key in ("hp", "mp"):
            return getattr(self.hpmp, "adapter", None)
        if key == "cooldown":
            return getattr(self.cooldown, "adapter", None)
        if key == "buff":
            return getattr(self.buff, "adapter", None)
        if key == "xp":
            return getattr(self.xp, "adapter", None)
        if key == "game":
            return getattr(self.capture, "grabber", None)
        return None

    def set_game_region(self, x: int, y: int, w: int, h: int) -> None:
        """게임 영역 — yolo + ocr 모두 이 영역 기준 crop.
        2026-04-25 v1 healer_worker.py:418-422 동일: SnapshotStore 에 절대 좌표
        저장. capture_watcher 가 매 tick read 하여 frame 을 crop 한다.
        adapter 에 set_region 메서드 있으면 추가 호출 (보통 불필요).
        2026-05-05 Cycle 4-10: ocr adapter 에도 set_game_region 호출
        (coord/map picker 역산용 game_region ref 저장)."""
        try:
            self.store.update(game_region_abs=(int(x), int(y), int(w), int(h)))
        except Exception:
            log.exception("game_region_abs store update fail")
        # ocr adapter 에 game_region 전달 (coord/map picker 역산용).
        ocr_ad = self._adapter_for("ocr")
        if ocr_ad is not None and hasattr(ocr_ad, "set_game_region"):
            try:
                ocr_ad.set_game_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("ocr set_game_region fail")
        g = self._adapter_for("game")
        if g is not None:
            for m in ("set_region", "set_game_region", "set_crop"):
                fn = getattr(g, m, None)
                if callable(fn):
                    try:
                        fn(int(x), int(y), int(w), int(h))
                        return
                    except Exception:
                        log.exception("game set_region fail")
                        return

    # 2026-05-05 Cycle 4-10 — coord/map picker setter (ocr adapter 위임).
    def set_coord_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = self._adapter_for("ocr")
        if ad is not None and hasattr(ad, "set_coord_region"):
            try:
                ad.set_coord_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("set_coord_region fail")

    def set_map_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = self._adapter_for("ocr")
        if ad is not None and hasattr(ad, "set_map_region"):
            try:
                ad.set_map_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("set_map_region fail")

    def clear_game_region(self) -> None:
        try:
            self.store.update(game_region_abs=None)
        except Exception:
            pass

    def set_cooldown_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = self._adapter_for("cooldown")
        if ad is not None:
            try:
                if hasattr(ad, "set_region"):
                    ad.set_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("cooldown set_region fail")

    def set_buff_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = self._adapter_for("buff")
        if ad is not None:
            try:
                if hasattr(ad, "set_region"):
                    ad.set_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("buff set_region fail")

    def set_chat_region(self, x: int, y: int, w: int, h: int) -> None:
        # chat 어댑터는 cooldown_watcher 와 별도. healer_worker_v2 는 chat
        # watcher 를 자체 보유하지 않으므로 executor.chat_adapter 가 있으면 위임.
        try:
            ad = getattr(self.executor, "chat_adapter", None)
            if ad is not None and hasattr(ad, "set_region"):
                ad.set_region(int(x), int(y), int(w), int(h))
        except Exception:
            log.exception("chat set_region fail")

    def set_xp_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = self._adapter_for("xp")
        if ad is not None and hasattr(ad, "set_region"):
            try:
                ad.set_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("xp set_region fail")

    def set_hp_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = self._adapter_for("hp")
        if ad is not None and hasattr(ad, "set_hp_region"):
            try:
                ad.set_hp_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("hp set_region fail")

    def set_mp_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = self._adapter_for("mp")
        if ad is not None and hasattr(ad, "set_mp_region"):
            try:
                ad.set_mp_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("mp set_region fail")

    def set_nick_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = self._adapter_for("cooldown")
        if ad is not None:
            try:
                fn = getattr(ad, "set_nick_region", None)
                if callable(fn):
                    fn(int(x), int(y), int(w), int(h))
                else:
                    # fallback: underlying ocr 에 직접
                    raw = getattr(ad, "underlying_ocr", None)
                    if raw is not None and hasattr(raw, "set_nick_region"):
                        raw.set_nick_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("nick set_region fail")

    def set_hp_max(self, n: int) -> None:
        ad = self._adapter_for("hp")
        if ad is not None and hasattr(ad, "set_hp_max"):
            try: ad.set_hp_max(int(n))
            except Exception: pass

    def set_mp_max(self, n: int) -> None:
        ad = self._adapter_for("mp")
        if ad is not None and hasattr(ad, "set_mp_max"):
            try: ad.set_mp_max(int(n))
            except Exception: pass

    # ---------------------------------------------------------------------
    # v1 호환 setter/property — facade 가 위임.
    # ---------------------------------------------------------------------
    def set_force_exit(self, direction: str, duration_sec: float = 1.5) -> None:
        """muscle DecisionState force_exit 강제 활성. v1 healer_worker.py
        force_exit 와 동치 (테스트/외부 트리거용).
        """
        if direction not in ("L", "R", "U", "D"):
            return
        try:
            import time as _t
            ds = self.muscle._dec_state
            ds.force_exit_until = _t.time() + float(duration_sec)
            ds.force_exit_dir = direction
            self.store.update(
                force_exit_active=True,
                force_exit_dir=direction,
                force_exit_remaining=float(duration_sec),
            )
            log.info("[FORCE-EXIT] dir=%s dur=%.2fs", direction, duration_sec)
        except Exception:
            log.exception("set_force_exit fail")

    def arm_tab_lock(self) -> None:
        """외부 트리거 — TAB-LOCK pending 활성화 (테스트용)."""
        try:
            arm_tab_lock_pending(self.integ_state)
        except Exception:
            log.exception("arm_tab_lock fail")

    def set_armed(self, on: bool) -> None:
        try:
            self.store.update(armed=bool(on))
        except Exception:
            log.exception("set_armed fail")

    def set_follow_only(self, on: bool) -> None:
        try:
            self.store.update(follow_only=bool(on))
        except Exception:
            log.exception("set_follow_only fail")

    def set_skill_enabled(self, name: str, on: bool) -> None:
        """v1 skill_enabled 토글 → rule_cfg 의 *_enabled 키와 PluginRegistry 둘 다 갱신."""
        key_map = {
            "공력증강": "gyoungryeok_enabled",
            "백호의희원": "baekho_enabled",
            "백호의희원첨": "baekho_enabled",
            "파력무참": "parlyuk_enabled",
            "파혼술": "parhon_enabled",
            "무장": "mujang_enabled",
            "보호": "boho_enabled",
            "자힐": "self_heal_enabled",
            "부활": "self_revive_enabled",
            # 2026-05-05 P0-2 fix: 이전엔 "boho_enabled" 로 잘못 매핑되어
            # 보호 토글과 묶여 있었음. 금강불체는 manual-only 정책이지만
            # rule_cfg 일관성 위해 geumgang_enabled 독립 키 사용.
            "금강불체": "geumgang_enabled",
        }
        rk = key_map.get(name)
        if rk:
            self.cfg.rule_cfg[rk] = bool(on)
        # 2026-05-05 P0-3 fix:
        #   PluginRegistry 는 클래스-수준 싱글턴 (모든 메서드 @classmethod).
        #   이전 `PluginRegistry.instance()` 는 AttributeError 였고
        #   외곽 `except Exception: pass` 가 silently swallow → 토글이
        #   PluginRegistry.params 에 한 번도 반영되지 않았음.
        #   classmethod 직접 호출 + 외곽 except 는 log.exception 으로.
        try:
            from ..core.plugin_registry import PluginRegistry
            for k in (f"rule.{name}.enabled", f"rule.{rk}.enabled" if rk else ""):
                if k:
                    try:
                        PluginRegistry.set_param(k, bool(on))
                    except Exception as e:
                        log.warning(
                            "[SET-SKILL] PluginRegistry.set_param fail key=%s err=%s",
                            k, e,
                        )
        except Exception:
            log.exception("[SET-SKILL] PluginRegistry write fail name=%s", name)

    def set_skill_vk(self, name: str, vk: int) -> None:
        """스킬 이름 → 가상키 매핑. _worker_state 에 저장 (sequences 가 ctx 로 read)."""
        try:
            ws = self._worker_state
            vks = ws.setdefault("skill_vks", {})
            vks[str(name)] = int(vk)
        except Exception:
            log.exception("set_skill_vk fail")

    def set_primary_vks(self, vks: list) -> None:
        try:
            self._worker_state["primary_vks"] = [int(v) for v in vks]
            # cycler slot 갱신 (있으면).
            if hasattr(self.cycler, "set_slots"):
                try:
                    self.cycler.set_slots([int(v) for v in vks])
                except Exception:
                    pass
        except Exception:
            log.exception("set_primary_vks fail")

    def set_parlyuk_offset(self, sec: float) -> None:
        try:
            self.cfg.rule_cfg["parlyuk_offset_sec"] = float(sec)
        except Exception:
            pass

    def set_self_heal_hp_thr(self, n: int) -> None:
        try:
            self.cfg.rule_cfg["self_heal_hp_thr"] = int(n)
        except Exception:
            pass

    def set_gyoungryeok_mp_thr(self, n: int) -> None:
        try:
            self.cfg.rule_cfg["gyoungryeok_mp_thr"] = int(n)
        except Exception:
            pass

    def set_own_skill_names(self, names) -> None:
        """힐러 본인 쿨 OCR 대상 스킬 이름 — cooldown adapter 에 위임."""
        try:
            ad = self._adapter_for("cooldown")
            if ad is not None and hasattr(ad, "set_target_skills"):
                ad.set_target_skills(list(names or []))
        except Exception:
            log.exception("set_own_skill_names fail")

    @property
    def last_fps(self) -> float:
        try:
            return float(self.store.read_field("fps", 0.0) or 0.0)
        except Exception:
            return 0.0

    @property
    def healer_coord(self):
        try:
            return self.store.read_field("healer_coord", None)
        except Exception:
            return None

    @property
    def healer_map(self) -> str:
        try:
            return self.store.read_field("healer_map", "") or ""
        except Exception:
            return ""

    def is_alive(self) -> bool:
        return bool(self._running)

    def latest_hpmp(self):
        try:
            ad = self._adapter_for("hp")
            if ad is not None and hasattr(ad, "latest"):
                return ad.latest()
        except Exception:
            pass
        return None

    def stats(self) -> dict:
        out = {
            "muscle": self.muscle.stats(),
            "executor": self.executor.stats(),
            "bus": self.bus.stats(),
            "action_log": self.action_log.stats(),
            "watchers": {
                w.watcher_name: w.stats() for w in (
                    self.capture, self.yolo, self.ocr, self.cooldown,
                    self.buff, self.hpmp, self.xp, self.udp,
                )
            },
        }
        if self.learner:
            out["learner"] = self.learner.stats()
        if self.alphago_runner:
            out["alphago_runner"] = self.alphago_runner.stats()
        if self.alphago_coach:
            out["alphago_coach"] = self.alphago_coach.stats()
        if self.uplink:
            out["uplink"] = self.uplink.stats()
        # v1 통합 분기 상태
        try:
            out["integ"] = {
                "tab_lock_pending_until": self.integ_state._pending_tab_lock_until,
                "post_self_heal_tab_until": self.integ_state._post_self_heal_tab_until,
                "parlyuk_buff_active": self.integ_state._parlyuk_buff_active,
                "last_map_change_ts": self.integ_state._last_map_change_ts,
                "f1_pend_active": bool(self.store.read().f1_pend_active),
                "force_exit_active": bool(self.store.read().force_exit_active),
            }
        except Exception:
            pass
        return out

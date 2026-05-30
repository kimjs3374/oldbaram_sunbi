# src_v2 — Human-Mimic Architecture

> Plan: `docs/01-plan/features/healer-mainloop-refactor.plan.md`
> Design: `docs/02-design/features/healer-mainloop-refactor.design.md`

src_v2는 **사람-유사 4영역 분리** 아키텍처로 메인 루프를 1-2ms로 슬림화하기 위한 빅뱅 재작성 결과물입니다. 기존 `src/`는 동결 보존 — 롤백 안전.

## 폴더 구조

```
src_v2/
├── core/         # event_bus, snapshot, plugin_registry, types
├── eyes/         # 백그라운드 감시 (capture/yolo/ocr/cooldown/hpmp/xp/udp)
├── brain/        # 룰 엔진 + 9개 룰 플러그인
├── hands/        # 키 디스패처 + 9개 시퀀스 플러그인 + numlock cycler
├── muscle/       # 메인 루프 (1-2ms 본체) + 순수 decide_direction
├── memory/       # action_log + ai_hook 인터페이스
├── learning/     # Phase 7 자기진화: @learnable + meta_learner + UCB1 + hot_apply
├── ui/           # publisher (15Hz emit, 메인 무영향)
├── workers/      # healer_worker_v2, attacker_worker_v2 (composition root)
├── adapters/     # src/* → v2 protocol bridge (배포 시 사용)
├── config/       # cfg loader + v1→v2 migration (lossless)
└── tests/        # pytest 단위 + 통합 시나리오
```

## 즉시 검증 방법

테스트는 외부 라이브러리(EasyOCR, ultralytics, mss, PyQt) **없이도 통과**합니다. 모든 외부 의존은 adapter로 분리되었고 테스트는 mock adapter로 self-verifiable.

```bash
cd D:\oldbaram
python -m pytest src_v2/tests/ -v
```

## 사용자 PC 배포 시 (Phase 8 단계)

healer_worker_v2 사용 예 (실제 src/ 어댑터 주입):

```python
from src_v2.workers import HealerWorkerV2, HealerConfig
from src_v2.adapters.grabber_adapter import SrcGrabberAdapter
from src_v2.adapters.yolo_adapter import SrcYoloAdapter
from src_v2.adapters.ocr_adapter import SrcOcrAdapter
from src_v2.adapters.hpmp_adapter import SrcHpMpAdapter
from src_v2.adapters.cooldown_adapter import SrcCooldownAdapter
from src_v2.adapters.udp_adapter import SrcUdpAdapter
from src_v2.adapters.keys_adapter import SrcKeysAdapter
from src_v2.config import load_v2_config, migrate_v1_to_v2

# 기존 src/ 인스턴스들 (이미 init된 것 그대로 활용)
from src.capture.screen import AsyncGrabber
from src.vision.yolo import YoloWrapper
# ...

cfg_v1 = load_existing_v1_cfg()  # 사용자 기존 cfg
cfg_v2 = migrate_v1_to_v2(cfg_v1)

worker = HealerWorkerV2(
    cfg=HealerConfig(...),  # cfg_v2["muscle"], cfg_v2["eyes"] 등 매핑
    grabber=SrcGrabberAdapter(grabber_instance),
    yolo=SrcYoloAdapter(yolo_instance),
    ocr=SrcOcrAdapter(coord_ocr_instance, map_ocr_instance),
    hpmp=SrcHpMpAdapter(hpmp_instance),
    cooldown=SrcCooldownAdapter(cd_instance),
    buff=SrcCooldownAdapter(buff_instance),
    udp=SrcUdpAdapter(udp_recv_instance),
    keys=SrcKeysAdapter(keys_module),
)
worker.start()
# ...
worker.stop()
```

## 핵심 보장

1. **메인 루프(muscle)** = snapshot read + decide_direction + key set_direction. 그 외 0.
2. **트리거(brain)** = 백그라운드 EventBus 핸들러. 메인 루프 영향 0.
3. **시퀀스(hands)** = 백그라운드 PriorityQueue worker. 메인 영향 0.
4. **UI 갱신** = UiPublisher 별도 스레드 15Hz. 메인 영향 0.

## 새 기능 추가 (5분 이내)

### 새 룰

`src_v2/brain/rules/my_rule.py` 1개 파일:

```python
from src_v2.core.plugin_registry import rule
from src_v2.core.types import CastRequest

@rule(name="my_rule", priority=25, topics=["eye.hp"])
def my_rule(snap, ctx):
    if snap.hp < 30 and "my_rule" not in ctx.in_progress:
        return CastRequest("my_seq", priority=25)
    return None
```

`src_v2/brain/rules/__init__.py`에 `from . import my_rule` 추가.

### 새 시퀀스

`src_v2/hands/sequences/my_seq.py`:

```python
from src_v2.core.plugin_registry import sequence
from ._common import tap, sleep_ms

@sequence("my_seq")
def my_seq(ctx):
    tap(ctx["_dispatcher"], "1")
    sleep_ms(50)
```

`src_v2/hands/sequences/__init__.py`에 import 추가.

기존 코드 0 수정.

## Phase 7 — 자기진화 (Self-Evolving)

룰 파라미터를 운영 데이터로 자동 튜닝하는 메타-러너입니다.

### 활성화

```python
cfg = HealerConfig(
    learning_enabled=True,
    learning_poll_sec=300.0,           # 5분마다 1 cycle
    learning_min_score=0.5,             # 점수 임계
    learning_rollback_sec=300.0,        # 적용 후 5분 뒤 fitness 재측정
    learning_regression_factor=1.1,     # 1.1배 이상 악화 시 롤백
)
worker = HealerWorkerV2(cfg=cfg, ...)
worker.start()  # MetaLearnerRunner 데몬 자동 기동
```

### 새 튜닝 대상 추가

```python
# src_v2/learning/learnable.py — builtin_learnables() 에 한 줄 추가
LearnableSpec(
    target_id="rule.my_rule.thr",       # rule.<rule_name>.<param>
    range=(0.1, 0.9),                    # 정상 튜닝 범위
    safety=(0.05, 0.99),                 # 강제 클램프 (range 밖 외측)
    fitness="higher_uptime",             # fitness.py 함수 이름
    default=0.5,
)
```

그리고 `decision.py` `_CFG_TO_TARGET`에 cfg key 매핑 추가하면 끝. rule handler는 이미
`ctx.cfg["my_rule_thr"]`로 읽고 있으므로 코드 수정 0.

### 데이터 흐름

```
ActionLog.all()
    -> MetaLearner.score_targets()    # frequency * volatility / (1+ok_ratio)
    -> Optimizer.propose()             # UCB1 over 5 arms in range
    -> HotApply.apply()                # COW set_param + token
    -> [wait rollback_window]
    -> Fitness.eval(after) vs baseline
        -> degraded -> rollback
        -> kept -> reward optimizer
```

### 검증

```bash
python -m pytest src_v2/tests/test_self_evolving.py -v
```

9 tests cover: @learnable 등록, COW set_param, fitness 함수, MetaLearner scoring,
UCB1 bandit, HotApply rollback/keep, Runner cycle, mock self-improvement scenario.

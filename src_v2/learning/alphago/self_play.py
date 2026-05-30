"""Self-play episode generation using PolicyNet (skill) + MovePolicyNet + EnvModel.

Two modes:
    play_episode():       skill-only (legacy, used by existing tests)
    play_episode_with_move(): skill + movement combined (new)
"""
from __future__ import annotations
from typing import List, Optional

import numpy as np

from .env_model import EnvModel
from .move_policy_net import MovePolicyNet, NUM_MOVE_ACTIONS
from .policy_net import PolicyNet, NUM_ACTIONS
from .replay_buffer import Transition


def play_episode(policy: PolicyNet,
                 env: EnvModel,
                 max_steps: int = 20,
                 epsilon: float = 0.1) -> List[Transition]:
    """Roll out one episode (skill-only). epsilon-greedy exploration."""
    if not env.fitted:
        return []
    s = env.sample_initial_state()
    out: List[Transition] = []
    for _ in range(max_steps):
        if np.random.random() < epsilon:
            a = int(np.random.randint(0, NUM_ACTIONS))
        else:
            probs = policy.forward(s)[0]
            probs = np.clip(probs, 1e-8, 1.0)
            probs = probs / probs.sum()
            a = int(np.random.choice(NUM_ACTIONS, p=probs))
        s_next, r = env.step(s, a)
        out.append(Transition(s=s.copy(), a=a, r=float(r), s_next=s_next.copy(), done=False))
        s = s_next
    return out


def play_episode_with_move(skill_policy: PolicyNet,
                           move_policy: MovePolicyNet,
                           env: EnvModel,
                           max_steps: int = 20,
                           epsilon: float = 0.1) -> List[Transition]:
    """Roll out an episode where each step has BOTH a skill and a move action.

    When skill != "wait" -> env.step (skill transition) is used.
    When skill == "wait" -> env.step_move (movement transition) is used.
    Both action indices are stored on the Transition for joint training.
    """
    if not env.fitted:
        return []
    s = env.sample_initial_state()
    out: List[Transition] = []
    for _ in range(max_steps):
        # skill action
        if np.random.random() < epsilon:
            a = int(np.random.randint(0, NUM_ACTIONS))
        else:
            sp = skill_policy.forward(s)[0]
            sp = np.clip(sp, 1e-8, 1.0)
            sp = sp / sp.sum()
            a = int(np.random.choice(NUM_ACTIONS, p=sp))
        # move action
        if np.random.random() < epsilon:
            am = int(np.random.randint(0, NUM_MOVE_ACTIONS))
        else:
            mp = move_policy.forward(s)[0]
            mp = np.clip(mp, 1e-8, 1.0)
            mp = mp / mp.sum()
            am = int(np.random.choice(NUM_MOVE_ACTIONS, p=mp))
        s_next, r = env.step_combined(s, a, am)
        out.append(Transition(
            s=s.copy(), a=a, a_move=am, r=float(r),
            s_next=s_next.copy(), done=False,
        ))
        s = s_next
    return out


__all__ = ["play_episode", "play_episode_with_move"]

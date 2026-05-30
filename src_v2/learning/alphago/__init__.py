"""AlphaGo-style self-evolving module for Healer macro.

Architecture (mapping to AlphaGo concepts):
    Snapshot      -> "board state"
    Action        -> "move" (10-class)
    Reward        -> hunting efficiency proxy from action_log
    PolicyNet     -> Snapshot -> action prob dist
    ValueNet      -> Snapshot -> expected future reward
    EnvModel      -> action_log Markov transition
    SelfPlay      -> simulate episodes with policy + env_model
    MCTS          -> UCT search (depth=5, sims=64) for online decisions
    Coach         -> orchestrator (daemon thread) — buffer fill, training, hot-swap

Pure numpy. NO PyTorch / sklearn. Drop-in to src_v2.learning.

External entry: AlphaGoRunner (for tests) and Coach (for HealerWorkerV2).
"""
from .feature_extractor import FeatureExtractor, FEATURE_DIM
from .nn_core import Linear, ReLU, Softmax, Tanh, Adam
from .policy_net import PolicyNet, ACTION_INDEX_TO_RULE, ACTION_RULE_TO_INDEX, NUM_ACTIONS
from .value_net import ValueNet
from .replay_buffer import ReplayBuffer, Transition
from .env_model import EnvModel
from .self_play import play_episode
from .mcts import MCTSNode, mcts_search
from .trainer import Trainer
from .coach import Coach
from .neural_rule import register_neural_advisor, AlphaGoRunner
from .weight_io import save_weights, load_weights, hot_swap

__all__ = [
    "FeatureExtractor", "FEATURE_DIM",
    "Linear", "ReLU", "Softmax", "Tanh", "Adam",
    "PolicyNet", "ValueNet",
    "ACTION_INDEX_TO_RULE", "ACTION_RULE_TO_INDEX", "NUM_ACTIONS",
    "ReplayBuffer", "Transition",
    "EnvModel",
    "play_episode",
    "MCTSNode", "mcts_search",
    "Trainer",
    "Coach",
    "AlphaGoRunner",
    "register_neural_advisor",
    "save_weights", "load_weights", "hot_swap",
]

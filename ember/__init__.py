"""Portable Ember v0.2 core package."""

from .core import HostCapabilities, SemanticEvent, VisualObservation, WorldState
from .embodiment import BodyAdapter, BodyState, EmbodimentController, ScreenTarget, SpriteBodyAdapter, body_state_for_speech
from .performance import SpeechPerformance
from .brain import EmberBrain, EventNormalizer, ResponsePlan, RunningBitManager
from .director import CuriosityThread, DirectorDecision, EmberDirector
from .runtime import AtomicJsonStore, SessionUsage, default_companion_memory
from .model_protocol import ModelAction, StreamedSpeechParser, parse_model_action
from .performance_plan import PerformancePlan, plan_performance
from .replay import ReplaySignal, replay_signals
from .overlay import EmberOverlay, ReactionImages

__all__ = [
    "HostCapabilities", "SemanticEvent", "VisualObservation", "WorldState",
    "BodyAdapter", "BodyState", "EmbodimentController", "ScreenTarget", "SpriteBodyAdapter", "body_state_for_speech", "SpeechPerformance",
    "EmberBrain", "EventNormalizer", "ResponsePlan", "RunningBitManager",
    "CuriosityThread", "DirectorDecision", "EmberDirector",
    "AtomicJsonStore", "SessionUsage", "default_companion_memory",
    "ModelAction", "StreamedSpeechParser", "parse_model_action",
    "PerformancePlan", "plan_performance", "ReplaySignal", "replay_signals",
    "EmberOverlay", "ReactionImages",
]

"""Deterministic model selection for Ember's hybrid companion brain."""


def hybrid_route(
    *, direct=False, tool_turn=False, has_game_event=False,
    companion_model="gpt-5.6-terra", router_model="gpt-5.6-luna",
    companion_effort="low", router_effort="none",
):
    if direct:
        return {
            "role": "companion",
            "model": companion_model,
            "reasoning_effort": companion_effort,
        }
    if has_game_event or not tool_turn:
        return {
            "role": "game_companion",
            "model": companion_model,
            "reasoning_effort": companion_effort,
        }
    return {
        "role": "media_router",
        "model": router_model,
        "reasoning_effort": router_effort,
    }

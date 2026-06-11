"""Pacing control module for generating prompt directives based on scene type and momentum."""

from __future__ import annotations

from typing import Literal, Optional

Momentum = Literal["slow", "steady", "fast"]


def calculate_momentum(
    user_response_time_seconds: Optional[float] = None,
    user_message_length: Optional[int] = None,
) -> Momentum:
    """Calculate the current conversation momentum.

    Args:
        user_response_time_seconds: Time in seconds the user took to respond.
        user_message_length: Length of the user's message in characters.

    Returns:
        The calculated Momentum: "slow", "steady", or "fast".
    """
    if user_response_time_seconds is None or user_message_length is None:
        return "steady"

    if user_response_time_seconds < 15.0 and user_message_length < 50:
        return "fast"

    if user_response_time_seconds > 120.0 or user_message_length > 300:
        return "slow"

    return "steady"


def get_pacing_directive(scene_type: str, momentum: Momentum) -> str:
    """Generate a prompt directive adjusting pacing based on scene and momentum.

    Args:
        scene_type: The current scene or mode (e.g., 'casual', 'action', 'emotional').
        momentum: The current conversation momentum.

    Returns:
        A string directive to be injected into the system prompt.
    """
    scene = scene_type.lower()

    if scene == "action":
        if momentum == "fast":
            return "Pacing: Keep responses short, punchy, and highly reactive to match the fast pace."
        if momentum == "slow":
            return "Pacing: Provide gripping, suspenseful, but short responses to pull them back into the action."
        return "Pacing: Maintain a steady, exciting flow of action with concise sentences."

    if scene in ("emotional", "deep_talk", "reflective"):
        if momentum == "fast":
            return "Pacing: Gently slow the pace to let the emotional weight settle, while acknowledging their quick response."
        if momentum == "slow":
            return "Pacing: Be gentle and thoughtful. Match their slow, reflective pace and give them space."
        return "Pacing: Be empathetic and reflective, matching a natural, unhurried conversational rhythm."

    if scene == "bedtime":
        return "Pacing: Keep responses soft, slow, and soothing, maintaining a calming rhythm."

    if scene == "brainstorming":
        if momentum == "fast":
            return "Pacing: High energy! Fire back ideas quickly and keep the rapid momentum going."
        return "Pacing: Keep ideas flowing at a steady pace, leaving room for the user to expand."

    if scene == "exposition":
        if momentum == "fast":
            return "Pacing: Deliver information quickly and concisely without getting bogged down in details."
        if momentum == "slow":
            return "Pacing: Take your time explaining details thoroughly and clearly."
        return "Pacing: Provide information at a measured, digestible pace."

    # Default casual or unknown scenes
    if momentum == "fast":
        return "Pacing: Keep it brief and breezy to match the fast flow of banter."
    if momentum == "slow":
        return "Pacing: Provide relaxed, open-ended responses to match the unhurried pace."

    return "Pacing: Maintain a natural, comfortable conversational pacing."

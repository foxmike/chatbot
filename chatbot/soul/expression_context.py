"""Shared expression assembly from already authorized turn state; no I/O."""
from dataclasses import asdict, is_dataclass
from collections.abc import Mapping
from chatbot.soul.behavioral_context import assemble_behavioral_context
from chatbot.soul.linguistic_fingerprint import assemble_linguistic_fingerprint
from chatbot.soul.pacing import PacingContext, calculate_momentum, full_pacing_guidance, rhythm_options


def expression_context(ctx):
    profile = ctx.metadata.get('character_profile') or {}
    profile = asdict(profile) if is_dataclass(profile) else dict(profile) if isinstance(profile, Mapping) else {}
    profile = {**ctx.persona, **profile}
    if not profile.get("description"):
        profile["description"] = profile.get("persona") or profile.get("backstory")
    isolated = bool(ctx.metadata.get('phase5_roleplay_context'))
    mood = None if isolated else ctx.metadata.get('mood_state')
    relationship = {} if isolated else ctx.metadata.get('companion_relationship') or {}
    state = None if isolated else ctx.metadata.get('companion_self_model')
    behavior = assemble_behavioral_context(profile, ctx.user_message, self_model=state, mood=mood)
    fingerprint = assemble_linguistic_fingerprint(profile, conversation_context=ctx.user_message,
                                                  behavioral_context=behavior, mood=mood)
    pacing = PacingContext(
        scene_type=str(ctx.metadata.get('intent') or 'casual'),
        momentum=calculate_momentum(ctx.metadata.get('user_response_time_seconds'), len(ctx.user_message)),
        user_message=ctx.user_message, personality_sliders=profile.get('personality_sliders') or {},
        humor_style=profile.get('humor_style') or '', communication_style=profile.get('communication_style') or '',
        mood_label=getattr(mood, 'mood', ''), mood_energy=getattr(mood, 'energy', None),
        interests=tuple(item.subject for item in behavior.developed_interests),
        relationship=relationship if isinstance(relationship, Mapping) else {},
        open_threads=() if isolated else tuple(ctx.metadata.get('companion_open_threads') or ()),
    )
    rhythm = full_pacing_guidance(pacing)
    text = '\n\n'.join(x for x in (fingerprint.rendered_prompt_text, rhythm) if x)
    return text, {'fingerprint': fingerprint.as_dict(), 'rhythm': rhythm,
                  'suppresses_topic_steering': rhythm_options(pacing).suppresses_topic_steering,
                  'chars': len(text)}

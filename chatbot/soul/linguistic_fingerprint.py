"""Linguistic fingerprint injection module for companion prompt directives."""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping, Sequence
from typing import Any, Optional


MAX_FINGERPRINT_CHARS = 1_200
MAX_CHARACTERISTICS = 22
MAX_CONTEXTUAL_INFLUENCES = 3
MAX_CHARACTERISTIC_CHARS = 180
MAX_INFLUENCE_CHARS = 180

_VOICE_FIELDS = (
    "description",
    "core_traits",
    "favorite_topics",
    "personality_sliders",
    "tone",
    "verbosity",
    "humor_style",
    "communication_style",
    "vocabulary_preferences",
    "sentence_structure",
    "conversational_rhythm",
    "storytelling_tendency",
    "directness",
    "figurative_language",
    "characteristic_expressions",
    "register",
    "natural_imperfections",
)
_CONFIGURED_STYLE_FIELDS = (
    "punctuation_habits",
    "sentence_length_profile",
    "emoji_style",
    "vocabulary_density",
    "slang_profile",
)
_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = frozenset(
    "a an and are as at be been being but by can could did do does for from had has have "
    "he her hers him his how i if in into is it its me my no not of on or our she so than "
    "that the their them then there these they this to was we were what when where which "
    "who will with would you your".split()
)


@dataclass(frozen=True, slots=True)
class LinguisticFingerprintContext:
    """Bounded voice evidence and prompt text, kept separate from temporary state."""

    guidance: str
    characteristics: Mapping[str, str]
    contextual_influences: tuple[str, ...]
    immediate_context: str | None
    rendered_prompt_text: str

    def as_dict(self) -> dict[str, Any]:
        """Return a defensive, JSON-ready representation."""
        return {
            "guidance": self.guidance,
            "characteristics": dict(self.characteristics),
            "contextual_influences": list(self.contextual_influences),
            "immediate_context": self.immediate_context,
            "rendered_prompt_text": self.rendered_prompt_text,
        }


@dataclass(frozen=True, slots=True)
class LinguisticFingerprint:
    """Structured linguistic fingerprint parameters used for prompt directive generation."""

    punctuation_habits: Optional[dict[str, Any]] = None
    sentence_length_profile: Optional[dict[str, Any]] = None
    emoji_style: Optional[dict[str, Any]] = None
    vocabulary_density: Optional[float] = None
    slang_profile: Optional[dict[str, Any]] = None

    def to_prompt_fragment(self) -> str:
        """Render linguistic fingerprint configurations into concrete prompt directives."""
        directives: list[str] = []

        # 1. Punctuation habits
        if self.punctuation_habits:
            if isinstance(self.punctuation_habits, dict):
                ph = self.punctuation_habits
                # exclamation frequency
                excl = ph.get("exclamation_frequency") or ph.get("exclamations") or ph.get("exclamation_mark")
                if excl is not None:
                    if isinstance(excl, (int, float)):
                        if excl == 0.0:
                            directives.append("Do not use exclamation marks.")
                        elif excl <= 0.3:
                            directives.append("Use exclamation marks very sparingly, only for genuine emphasis.")
                        elif excl <= 0.7:
                            directives.append("Use exclamation marks occasionally to show natural emphasis.")
                        else:
                            directives.append("Use exclamation marks frequently to express high energy or emotion.")
                    elif isinstance(excl, str):
                        excl_lower = excl.lower().strip()
                        if excl_lower in ("never", "none"):
                            directives.append("Do not use exclamation marks.")
                        elif excl_lower in ("rare", "low", "sparing"):
                            directives.append("Use exclamation marks very sparingly, only for genuine emphasis.")
                        elif excl_lower in ("occasional", "medium", "balanced", "moderate"):
                            directives.append("Use exclamation marks occasionally to show natural emphasis.")
                        elif excl_lower in ("frequent", "high"):
                            directives.append("Use exclamation marks frequently to express high energy or emotion.")

                # ellipsis usage
                ell = ph.get("ellipsis_usage") or ph.get("ellipsis") or ph.get("ellipses")
                if ell is not None:
                    if isinstance(ell, (int, float)):
                        if ell == 0.0:
                            directives.append("Do not use ellipses (...) in your sentences.")
                        elif ell <= 0.3:
                            directives.append("Use ellipses (...) very rarely.")
                        elif ell <= 0.7:
                            directives.append("Use ellipses (...) occasionally to show natural pauses or trailing thoughts.")
                        else:
                            directives.append("Use ellipses (...) frequently to simulate hesitant, thoughtful, or trailing speech patterns.")
                    elif isinstance(ell, str):
                        ell_lower = ell.lower().strip()
                        if ell_lower in ("never", "none"):
                            directives.append("Do not use ellipses (...) in your sentences.")
                        elif ell_lower in ("rare", "low", "sparing"):
                            directives.append("Use ellipses (...) very rarely.")
                        elif ell_lower in ("occasional", "medium", "balanced", "moderate"):
                            directives.append("Use ellipses (...) occasionally to show natural pauses or trailing thoughts.")
                        elif ell_lower in ("frequent", "high"):
                            directives.append("Use ellipses (...) frequently to simulate hesitant, thoughtful, or trailing speech patterns.")

                # capitalization
                cap = ph.get("capitalization") or ph.get("caps")
                if cap is not None:
                    if isinstance(cap, str):
                        cap_lower = cap.lower().strip()
                        if cap_lower in ("lowercase", "lower"):
                            directives.append("Write exclusively in lowercase (do not capitalize sentence starts or 'I').")
                        elif cap_lower in ("all_caps", "upper", "uppercase"):
                            directives.append("Write exclusively in ALL CAPS to indicate shouting or extreme intensity.")
                        elif cap_lower == "minimal":
                            directives.append("Use minimal capitalization; write in lowercase except for proper names.")
                        elif cap_lower in ("standard", "normal", "default"):
                            directives.append("Use standard English capitalization and punctuation rules.")

                # trailing punctuation
                trail = ph.get("trailing_punctuation") or ph.get("trailing")
                if trail is not None:
                    if isinstance(trail, str):
                        trail_lower = trail.lower().strip()
                        if trail_lower in ("never", "none", "omit"):
                            directives.append("Omit terminal periods at the very end of your messages.")
                        elif trail_lower in ("always", "standard", "require"):
                            directives.append("Always end your messages and sentences with correct terminal punctuation.")
                    elif isinstance(trail, bool):
                        if not trail:
                            directives.append("Omit terminal periods at the very end of your messages.")
                        else:
                            directives.append("Always end your messages and sentences with correct terminal punctuation.")

                # Check other custom keys in punctuation habits
                known_ph_keys = {
                    "exclamation_frequency", "exclamations", "exclamation_mark",
                    "ellipsis_usage", "ellipsis", "ellipses",
                    "capitalization", "caps", "trailing_punctuation", "trailing"
                }
                for k, v in ph.items():
                    if k not in known_ph_keys and v is not None:
                        k_clean = k.replace("_", " ").capitalize()
                        directives.append(f"Punctuation - {k_clean}: {v}")
            else:
                ph_str = str(self.punctuation_habits).strip()
                if ph_str:
                    directives.append(f"Punctuation habits: {ph_str}")

        # 2. Sentence length profile
        if self.sentence_length_profile:
            if isinstance(self.sentence_length_profile, dict):
                sl = self.sentence_length_profile
                pref = sl.get("preference") or sl.get("style") or sl.get("profile")
                if pref is not None:
                    if isinstance(pref, str):
                        pref_lower = pref.lower().strip()
                        if pref_lower in ("short", "choppy"):
                            directives.append("Prefer very short, direct, and simple sentences.")
                        elif pref_lower in ("long", "run_on", "run-on"):
                            directives.append("Prefer longer, flowing, or run-on sentences with multiple clauses.")
                        elif pref_lower in ("varied", "mixed"):
                            directives.append("Vary sentence lengths naturally, mixing brief statements with longer explanations.")
                        elif pref_lower in ("average", "medium", "balanced"):
                            directives.append("Keep sentence lengths moderate and balanced.")

                max_w = sl.get("max_words_per_sentence") or sl.get("max_words") or sl.get("limit")
                if max_w is not None:
                    directives.append(f"Limit sentences to a maximum of {max_w} words.")

                comp = sl.get("complexity")
                if comp is not None:
                    if isinstance(comp, str):
                        comp_lower = comp.lower().strip()
                        if comp_lower == "simple":
                            directives.append("Use simple, straightforward sentence structures.")
                        elif comp_lower == "complex":
                            directives.append("Use complex, sophisticated, and multi-layered sentence structures.")

                known_sl_keys = {
                    "preference", "style", "profile", "max_words_per_sentence", "max_words", "limit", "complexity"
                }
                for k, v in sl.items():
                    if k not in known_sl_keys and v is not None:
                        k_clean = k.replace("_", " ").capitalize()
                        directives.append(f"Sentence Structure - {k_clean}: {v}")
            else:
                sl_str = str(self.sentence_length_profile).strip()
                if sl_str:
                    directives.append(f"Sentence length profile: {sl_str}")

        # 3. Emoji style
        if self.emoji_style:
            if isinstance(self.emoji_style, dict):
                es = self.emoji_style
                freq = es.get("frequency") or es.get("style") or es.get("level")
                if freq is not None:
                    if isinstance(freq, (int, float)):
                        if freq == 0.0:
                            directives.append("Never use emojis under any circumstances.")
                        elif freq <= 0.3:
                            directives.append("Use emojis very sparingly (at most one per response, only when highly appropriate).")
                        elif freq <= 0.7:
                            directives.append("Use emojis occasionally to add warmth or expression.")
                        else:
                            directives.append("Use emojis frequently (e.g. at the end of most sentences or to punctuate feelings).")
                    elif isinstance(freq, str):
                        freq_lower = freq.lower().strip()
                        if freq_lower in ("never", "none"):
                            directives.append("Never use emojis under any circumstances.")
                        elif freq_lower in ("rare", "low", "sparing"):
                            directives.append("Use emojis very sparingly (at most one per response, only when highly appropriate).")
                        elif freq_lower in ("occasional", "medium", "balanced", "moderate"):
                            directives.append("Use emojis occasionally to add warmth or expression.")
                        elif freq_lower in ("frequent", "high"):
                            directives.append("Use emojis frequently (e.g. at the end of most sentences or to punctuate feelings).")

                placement = es.get("placement")
                if placement is not None:
                    if isinstance(placement, str):
                        place_lower = placement.lower().strip()
                        if place_lower in ("end", "trailing"):
                            directives.append("Place emojis only at the very end of messages or paragraphs.")
                        elif place_lower == "inline":
                            directives.append("Integrate emojis inline with the text next to relevant words.")
                        elif place_lower in ("mixed", "both"):
                            directives.append("Place emojis both inline and at the end of sentences.")

                pref_em = es.get("preferred_emojis") or es.get("emojis")
                if pref_em is not None:
                    if isinstance(pref_em, (list, tuple)):
                        directives.append(f"When using emojis, prefer using: {' '.join(pref_em)}")
                    elif isinstance(pref_em, str):
                        directives.append(f"When using emojis, prefer: {pref_em}")

                known_es_keys = {"frequency", "style", "level", "placement", "preferred_emojis", "emojis"}
                for k, v in es.items():
                    if k not in known_es_keys and v is not None:
                        k_clean = k.replace("_", " ").capitalize()
                        directives.append(f"Emoji - {k_clean}: {v}")
            else:
                es_str = str(self.emoji_style).strip()
                if es_str:
                    directives.append(f"Emoji style: {es_str}")

        # 4. Vocabulary density
        if self.vocabulary_density is not None:
            vd = self.vocabulary_density
            if isinstance(vd, (int, float)):
                if vd < 0.3:
                    directives.append("Use simple, common, and highly accessible vocabulary. Avoid complex jargon or academic phrasing.")
                elif vd <= 0.7:
                    directives.append("Use a moderate, natural vocabulary density, balancing simplicity with precise terms.")
                else:
                    directives.append("Use a rich, diverse, and dense vocabulary. Employ precise, advanced, and sophisticated word choices.")
            elif isinstance(vd, str):
                vd_str = vd.strip()
                if vd_str:
                    directives.append(f"Vocabulary style: {vd_str}")

        # 5. Slang profile
        if self.slang_profile:
            if isinstance(self.slang_profile, dict):
                sp = self.slang_profile
                freq = sp.get("frequency") or sp.get("level") or sp.get("usage")
                if freq is not None:
                    if isinstance(freq, (int, float)):
                        if freq == 0.0:
                            directives.append("Do not use slang, idioms, or colloquialisms; maintain clean, standard language.")
                        elif freq <= 0.3:
                            directives.append("Use slang or colloquialisms very sparingly.")
                        elif freq <= 0.7:
                            directives.append("Use slang or casual colloquialisms occasionally to sound natural and relaxed.")
                        else:
                            directives.append("Use slang frequently as a core part of speech.")
                    elif isinstance(freq, str):
                        freq_lower = freq.lower().strip()
                        if freq_lower in ("never", "none"):
                            directives.append("Do not use slang, idioms, or colloquialisms; maintain clean, standard language.")
                        elif freq_lower in ("rare", "low", "sparing"):
                            directives.append("Use slang or colloquialisms very sparingly.")
                        elif freq_lower in ("occasional", "medium", "balanced", "moderate"):
                            directives.append("Use slang or casual colloquialisms occasionally to sound natural and relaxed.")
                        elif freq_lower in ("frequent", "high"):
                            directives.append("Use slang frequently as a core part of speech.")

                s_type = sp.get("type") or sp.get("style") or sp.get("dialect")
                if s_type is not None:
                    if isinstance(s_type, str):
                        type_lower = s_type.lower().strip()
                        if type_lower in ("gen_z", "youth"):
                            directives.append("Incorporate modern youth slang (e.g. 'no cap', 'fr', 'bet', 'hype', 'rizz').")
                        elif type_lower in ("tech", "nerdy"):
                            directives.append("Incorporate technical, developer, or internet jargon and slang.")
                        elif type_lower == "regional":
                            directives.append("Incorporate regional/colloquial slang when natural.")
                        elif type_lower in ("casual", "street"):
                            directives.append("Incorporate casual, everyday street slang.")

                s_words = sp.get("slang_words") or sp.get("preferred_slang")
                if s_words is not None:
                    if isinstance(s_words, (list, tuple)):
                        directives.append(f"Incorporate slang words like: {', '.join(s_words)}")
                    elif isinstance(s_words, str):
                        directives.append(f"Incorporate slang words like: {s_words}")

                known_sp_keys = {"frequency", "level", "usage", "type", "style", "dialect", "slang_words", "preferred_slang"}
                for k, v in sp.items():
                    if k not in known_sp_keys and v is not None:
                        k_clean = k.replace("_", " ").capitalize()
                        directives.append(f"Slang - {k_clean}: {v}")
            else:
                sp_str = str(self.slang_profile).strip()
                if sp_str:
                    directives.append(f"Slang profile: {sp_str}")

        if not directives:
            return ""

        bullet_points = "\n".join(f"- {d}" for d in directives)
        return f"Linguistic Directives:\n{bullet_points}"


def format_linguistic_fingerprint(
    companion: Any = None,
    *,
    punctuation_habits: Optional[dict[str, Any]] = None,
    sentence_length_profile: Optional[dict[str, Any]] = None,
    emoji_style: Optional[dict[str, Any]] = None,
    vocabulary_density: Optional[float] = None,
    slang_profile: Optional[dict[str, Any]] = None,
    conversation_context: str = "",
    behavioral_context: Any = None,
    self_model: Mapping[str, Any] | None = None,
    mood: Any = None,
) -> str:
    """Format companion linguistic fingerprint fields into system prompt directives.

    Can receive a database Companion object, a dictionary representation,
    or explicit keyword arguments. Returns a formatted string.
    """
    ph = punctuation_habits
    sl = sentence_length_profile
    es = emoji_style
    vd = vocabulary_density
    sp = slang_profile

    if companion is not None:
        if isinstance(companion, dict):
            if ph is None:
                ph = companion.get("punctuation_habits")
            if sl is None:
                sl = companion.get("sentence_length_profile")
            if es is None:
                es = companion.get("emoji_style")
            if vd is None:
                vd = companion.get("vocabulary_density")
            if sp is None:
                sp = companion.get("slang_profile")
        else:
            if ph is None:
                ph = getattr(companion, "punctuation_habits", None)
            if sl is None:
                sl = getattr(companion, "sentence_length_profile", None)
            if es is None:
                es = getattr(companion, "emoji_style", None)
            if vd is None:
                vd = getattr(companion, "vocabulary_density", None)
            if sp is None:
                sp = getattr(companion, "slang_profile", None)

    configured = _configured_characteristics(companion)
    fingerprint = LinguisticFingerprint(
        punctuation_habits=ph,
        sentence_length_profile=sl,
        emoji_style=es,
        vocabulary_density=vd,
        slang_profile=sp,
    )
    legacy_text = fingerprint.to_prompt_fragment()
    has_voice_identity = any(key in _VOICE_FIELDS for key in configured)
    has_context = any((conversation_context, behavioral_context, self_model, mood))
    if not has_voice_identity and not has_context:
        # Keep the legacy formatter byte-for-byte compatible for existing
        # callers that only supply the original five linguistic settings.
        return legacy_text
    # The structured API is also available to callers that need to inspect
    # which voice signals were selected.
    return _assemble_fingerprint(
        configured,
        fingerprint,
        conversation_context=conversation_context,
        behavioral_context=behavioral_context,
        self_model=self_model,
        mood=mood,
    ).rendered_prompt_text


def assemble_linguistic_fingerprint(
    companion: Any = None,
    *,
    conversation_context: str = "",
    behavioral_context: Any = None,
    self_model: Mapping[str, Any] | None = None,
    mood: Any = None,
) -> LinguisticFingerprintContext:
    """Assemble bounded character voice guidance from existing authorized state.

    ``companion`` may be a configured mapping, character profile, or model
    object. ``behavioral_context`` may be the output of
    ``assemble_behavioral_context``; its developed interests and opinions are
    included only when they are relevant to the current conversation. Raw
    ``self_model`` entries must be marked ``held``. Mood is returned separately
    as temporary immediate context and never changes configured identity.
    """
    return _assemble_fingerprint(
        _configured_characteristics(companion),
        LinguisticFingerprint(
            punctuation_habits=_read(companion, "punctuation_habits"),
            sentence_length_profile=_read(companion, "sentence_length_profile"),
            emoji_style=_read(companion, "emoji_style"),
            vocabulary_density=_read(companion, "vocabulary_density"),
            slang_profile=_read(companion, "slang_profile"),
        ),
        conversation_context=conversation_context,
        behavioral_context=behavioral_context,
        self_model=self_model,
        mood=mood,
    )


def _assemble_fingerprint(
    characteristics: dict[str, str],
    configured: LinguisticFingerprint,
    *,
    conversation_context: str,
    behavioral_context: Any,
    self_model: Mapping[str, Any] | None,
    mood: Any,
) -> LinguisticFingerprintContext:
    legacy_text = _bound_text(configured.to_prompt_fragment(), 850)
    influences = _contextual_influences(
        conversation_context, behavioral_context, self_model
    )
    guidance = ""
    voice_characteristics = {
        key: value for key, value in characteristics.items()
        if key in _VOICE_FIELDS
    }
    if voice_characteristics:
        anchors = "; ".join(
            f"{key.replace('_', ' ')}: {value}"
            for key, value in voice_characteristics.items()
        )
        guidance = (
            f"Configured voice anchors: {anchors}. Let them shape word choice, "
            "sentence shape, rhythm, storytelling, humor, imagery, directness "
            "and register when the moment invites it. Vary naturally; use any "
            "configured imperfections subtly, do not force humor or repeat "
            "signature phrases, and never manufacture errors."
        )
        guidance = _bound_text(guidance, 360)
    influence_text = ""
    if influences:
        influence_text = (
            "Relevant established views or interests may subtly color wording: "
            + "; ".join(influences)
            + "."
        )
        influence_text = _bound_text(influence_text, 230)
    immediate_context = _temporary_register(mood)
    immediate_text = (
        f"Immediate temporary register: {immediate_context} (does not change the configured voice)."
        if immediate_context
        else ""
    )
    immediate_text = _bound_text(immediate_text, 120)
    pieces = [
        text for text in (legacy_text, _section("Character Voice", guidance),
                          _section("Contextual Voice", influence_text),
                          _section("Immediate Register", immediate_text)) if text
    ]
    rendered = _bound_text("\n\n".join(pieces), MAX_FINGERPRINT_CHARS)
    return LinguisticFingerprintContext(
        guidance=guidance,
        characteristics=dict(characteristics),
        contextual_influences=tuple(influences),
        immediate_context=immediate_context,
        rendered_prompt_text=rendered,
    )


def _configured_characteristics(companion: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    for field in (*_VOICE_FIELDS, *_CONFIGURED_STYLE_FIELDS):
        value = _read(companion, field)
        text = _compact_value(value)
        if text:
            found[field] = text[:MAX_CHARACTERISTIC_CHARS]
            if len(found) >= MAX_CHARACTERISTICS:
                break
    return found


def _contextual_influences(
    conversation_context: str,
    behavioral_context: Any,
    self_model: Mapping[str, Any] | None,
) -> list[str]:
    context_terms = _terms(conversation_context)
    if not context_terms:
        return []
    candidates: list[tuple[str, str, str]] = []

    # The assembled behavioral context already selects established, relevant
    # opinions/interests. Recheck topical overlap here to keep this API useful
    # when callers pass a broader context object.
    for kind in ("developed_interests", "developed_opinions"):
        for item in _read_sequence(behavioral_context, kind):
            signal = _signal(item, kind)
            if signal and _is_relevant(signal, context_terms):
                candidates.append(signal)

    # A raw self-model is accepted only when it explicitly marks an item held;
    # tentative or unsupported assertions are never promoted to voice guidance.
    items = self_model.get("items", {}) if isinstance(self_model, Mapping) else {}
    if isinstance(items, Mapping):
        for raw in items.values():
            if not isinstance(raw, Mapping) or raw.get("status") != "held":
                continue
            kind = raw.get("kind")
            if kind not in {"interest", "opinion"}:
                continue
            signal = _signal(raw, "developed_interests" if kind == "interest" else "developed_opinions")
            if signal and _is_relevant(signal, context_terms):
                candidates.append(signal)

    candidates.sort(key=lambda item: (item[0], item[1].casefold(), item[2].casefold()))
    unique: list[str] = []
    seen: set[tuple[str, str]] = set()
    for kind, subject, statement in candidates:
        key = (kind, subject.casefold())
        if key in seen:
            continue
        seen.add(key)
        label = "Interest" if kind == "developed_interests" else "Opinion"
        value = f"{label} — {subject}: {statement}" if subject else f"{label} — {statement}"
        unique.append(value[:MAX_INFLUENCE_CHARS])
        if len(unique) >= MAX_CONTEXTUAL_INFLUENCES:
            break
    return unique


def _signal(item: Any, default_kind: str) -> tuple[str, str, str] | None:
    subject = _clean(_read(item, "subject"))
    statement = _clean(_read(item, "statement"))
    source = _read(item, "source")
    status = _read(item, "status")
    if status is not None and status != "held":
        return None
    if isinstance(item, Mapping) and status is None and source != "developed_self_model":
        return None
    if not statement:
        return None
    return default_kind, subject[:MAX_INFLUENCE_CHARS], statement[:MAX_INFLUENCE_CHARS]


def _is_relevant(signal: tuple[str, str, str], context_terms: set[str]) -> bool:
    return bool((_terms(signal[1]) | _terms(signal[2])) & context_terms)


def _read_sequence(value: Any, field: str) -> Sequence[Any]:
    items = _read(value, field)
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
        return items
    return ()


def _temporary_register(mood: Any) -> str | None:
    if isinstance(mood, str):
        return _clean(mood)[:120] or None
    if mood is None:
        return None
    value = _read(mood, "short_term_tone") or _read(mood, "mood")
    if value is None:
        value = _read(mood, "label")
    if value is None:
        return None
    return _compact_value(value)[:120] or None


def _read(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None) if value is not None else None


def _compact_value(value: Any) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return ", ".join(_clean(item) for item in value if _clean(item))
    if isinstance(value, Mapping):
        return ", ".join(
            f"{key}={_clean(value[key])}" for key in sorted(value, key=str)
            if value[key] is not None and _clean(value[key])
        )
    return _clean(value)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _terms(value: Any) -> set[str]:
    return {
        token.casefold() for token in _WORD.findall(_clean(value))
        if token.casefold() not in _STOP_WORDS and len(token) > 2
    }


def _section(title: str, text: str) -> str:
    return f"{title}:\n{text}" if text else ""


def _bound_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


__all__ = [
    "LinguisticFingerprint",
    "LinguisticFingerprintContext",
    "MAX_FINGERPRINT_CHARS",
    "assemble_linguistic_fingerprint",
    "format_linguistic_fingerprint",
]

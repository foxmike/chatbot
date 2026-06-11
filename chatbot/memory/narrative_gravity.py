from typing import Any, Mapping

class NarrativeGravityWeighter:
    """Weights retrieved memories based on emotional salience and narrative gravity."""

    SALIENT_FLAGS = {"is_key_event", "narrative_milestone", "high_emotion", "core_memory"}
    EMOTION_KEYWORDS = {
        "angry", "joyful", "sad", "fearful", "surprised", "disgusted", 
        "love", "grief", "euphoria", "trauma", "breakthrough", "devastated", "ecstatic"
    }

    def __init__(self, multiplier: float = 1.25, offset: float = 0.1):
        """
        Args:
            multiplier: The factor by which to multiply the base score if salient.
            offset: An absolute value to add to the score if salient.
        """
        self.multiplier = multiplier
        self.offset = offset

    def weight_score(self, metadata: Mapping[str, Any], base_score: float) -> float:
        """
        Adjusts the similarity score based on metadata indicators of narrative gravity.
        """
        if not metadata:
            return base_score

        is_salient = self._check_salience(metadata)

        if is_salient:
            return (base_score * self.multiplier) + self.offset
            
        return base_score

    def _check_salience(self, metadata: Mapping[str, Any]) -> bool:
        """
        Performs metadata parsing and salience checks to determine if an event is a key narrative event.
        """
        # 1. Check explicit boolean flags
        for flag in self.SALIENT_FLAGS:
            val = metadata.get(flag)
            if val is True or str(val).lower() == "true":
                return True

        # 2. Check tags for salient flags or emotional keywords
        tags = metadata.get("tags", [])
        if isinstance(tags, (list, set, tuple)):
            for tag in tags:
                tag_str = str(tag).lower()
                if tag_str in self.SALIENT_FLAGS or tag_str in self.EMOTION_KEYWORDS:
                    return True
        elif isinstance(tags, str):
            tag_str = tags.lower()
            if tag_str in self.SALIENT_FLAGS or tag_str in self.EMOTION_KEYWORDS:
                return True

        # 3. Check explicit emotion or mood fields
        for emotion_field in ["emotion", "mood", "sentiment"]:
            val = metadata.get(emotion_field)
            if isinstance(val, str) and val.lower() in self.EMOTION_KEYWORDS:
                return True

        # 4. Check memory importance field
        importance = str(metadata.get("memory_importance", "")).lower()
        if importance in ("high", "critical", "durable", "core"):
            return True

        return False

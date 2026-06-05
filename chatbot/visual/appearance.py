"""Appearance management for visual identity continuity. Provides a canonical appearance profile with a character seed lock, outfit tracking, and visual trait customization."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


class AppearanceProfile(BaseModel):
    character_seed: int = 42
    character_description: str = ""
    visual_traits: list[str] = Field(default_factory=list)
    art_style: str = "digital illustration"
    color_palette: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str | None = None

    model_config = ConfigDict(frozen=False)

    def __init__(self, **data: object) -> None:
        data.setdefault("created_at", _utcnow_iso())
        super().__init__(**data)

    def touch(self) -> None:
        self.updated_at = _utcnow_iso()


class Outfit(BaseModel):
    outfit_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = "default"
    description: str = ""
    visual_tags: list[str] = Field(default_factory=list)
    is_active: bool = False
    created_at: str = Field(default_factory=_utcnow_iso)


class AppearanceContinuitySnapshot(BaseModel):
    character_seed: int
    character_description: str
    visual_traits: list[str]
    art_style: str
    color_palette: list[str]
    active_outfit: Outfit | None
    prompt: str
    negative_prompt: str


class OutfitManager:
    def __init__(self, outfits: list[Outfit] | None = None) -> None:
        if outfits is None:
            outfits = [
                Outfit(
                    name="default",
                    description="Default outfit",
                    is_active=True,
                )
            ]
        self._outfits = list(outfits)

    def add_outfit(self, name: str, description: str, visual_tags: list[str]) -> Outfit:
        outfit = Outfit(
            name=name,
            description=description,
            visual_tags=list(visual_tags),
            is_active=False,
        )
        self._outfits.append(outfit)
        return outfit

    def activate_outfit(self, outfit_id: str) -> Outfit:
        active_outfit = None
        for outfit in self._outfits:
            if outfit.outfit_id == outfit_id:
                active_outfit = outfit
                break

        if active_outfit is None:
            raise ValueError(f"Outfit not found: {outfit_id}")

        for outfit in self._outfits:
            outfit.is_active = outfit.outfit_id == outfit_id
        return active_outfit

    def get_active_outfit(self) -> Outfit | None:
        for outfit in self._outfits:
            if outfit.is_active:
                return outfit
        return None

    def list_outfits(self) -> list[Outfit]:
        return list(self._outfits)

    def remove_outfit(self, outfit_id: str) -> None:
        if len(self._outfits) == 1:
            raise ValueError("Cannot remove the only remaining outfit")

        outfit = next((item for item in self._outfits if item.outfit_id == outfit_id), None)
        if outfit is None:
            raise ValueError(f"Outfit not found: {outfit_id}")

        was_active = outfit.is_active
        self._outfits.remove(outfit)
        if was_active and self._outfits:
            self._outfits[0].is_active = True


class AppearanceManager:
    DEFAULT_ANTI_TRAITS = [
        "inconsistent character identity",
        "different face",
        "different hair",
        "different eye color",
        "outfit inconsistency",
        "blurry",
        "low quality",
        "distorted anatomy",
        "extra limbs",
        "deformed hands",
        "text artifacts",
        "watermark",
    ]

    def __init__(
        self,
        profile: AppearanceProfile | None = None,
        outfit_manager: OutfitManager | None = None,
    ) -> None:
        self._profile = profile or AppearanceProfile()
        self._outfit_manager = outfit_manager or OutfitManager()

    def get_profile(self) -> AppearanceProfile:
        return self._profile

    def update_character_description(self, description: str) -> None:
        self._profile.character_description = description
        self._profile.touch()

    def update_visual_traits(self, traits: list[str]) -> None:
        self._profile.visual_traits = list(traits)
        self._profile.touch()

    def update_art_style(self, style: str) -> None:
        self._profile.art_style = style
        self._profile.touch()

    def update_color_palette(self, palette: list[str]) -> None:
        self._profile.color_palette = list(palette)
        self._profile.touch()

    def build_generation_prompt(
        self,
        scene_description: str,
        negative_prompt: str | None = None,
    ) -> tuple[str, str, int]:
        prompt_parts = [scene_description]

        if self._profile.character_description:
            prompt_parts.append(f"Character: {self._profile.character_description}")
        if self._profile.visual_traits:
            prompt_parts.append(f"Visual traits: {', '.join(self._profile.visual_traits)}")
        if self._profile.art_style:
            prompt_parts.append(f"Art style: {self._profile.art_style}")
        if self._profile.color_palette:
            prompt_parts.append(f"Color palette: {', '.join(self._profile.color_palette)}")

        active_outfit = self._outfit_manager.get_active_outfit()
        if active_outfit is not None:
            if active_outfit.description:
                prompt_parts.append(f"Outfit: {active_outfit.description}")
            if active_outfit.visual_tags:
                prompt_parts.append(f"Outfit tags: {', '.join(active_outfit.visual_tags)}")

        full_prompt = ". ".join(part.strip() for part in prompt_parts if part.strip())
        full_negative = negative_prompt or ", ".join(self.DEFAULT_ANTI_TRAITS)
        return full_prompt, full_negative, self._profile.character_seed

    def build_continuity_snapshot(
        self,
        scene_description: str,
        negative_prompt: str | None = None,
    ) -> AppearanceContinuitySnapshot:
        prompt, full_negative, seed = self.build_generation_prompt(
            scene_description,
            negative_prompt=negative_prompt,
        )
        active_outfit = self._outfit_manager.get_active_outfit()
        return AppearanceContinuitySnapshot(
            character_seed=seed,
            character_description=self._profile.character_description,
            visual_traits=list(self._profile.visual_traits),
            art_style=self._profile.art_style,
            color_palette=list(self._profile.color_palette),
            active_outfit=active_outfit.model_copy(deep=True)
            if active_outfit is not None
            else None,
            prompt=prompt,
            negative_prompt=full_negative,
        )

    def lock_seed(self, seed: int) -> int:
        self._profile.character_seed = seed
        self._profile.touch()
        return self._profile.character_seed

    def get_outfit_manager(self) -> OutfitManager:
        return self._outfit_manager


def stable_character_seed(identity_key: str) -> int:
    normalized_key = identity_key.strip()
    if not normalized_key:
        return AppearanceProfile().character_seed
    digest = sha256(normalized_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="big", signed=False)


def stable_outfit_id(identity_key: str, outfit_name: str) -> str:
    normalized_identity = identity_key.strip() or "default"
    normalized_outfit = outfit_name.strip() or "default"
    return str(
        uuid5(
            NAMESPACE_URL,
            f"chatbot:outfit:{normalized_identity}:{normalized_outfit}",
        )
    )


__all__ = [
    "AppearanceContinuitySnapshot",
    "AppearanceManager",
    "AppearanceProfile",
    "Outfit",
    "OutfitManager",
    "stable_character_seed",
    "stable_outfit_id",
]

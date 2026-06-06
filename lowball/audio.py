import os
import random
import numpy as np
import pygame
from lowball import config

class SoundBank:
    def __init__(self, root_dir: str):
        self.enabled = False
        self.last_drop_ms = -10_000
        self.last_chain_ms = -10_000
        self.drop_sounds = []
        self.chain_sounds = []

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            self.drop_sounds = self._load_pitch_variants(
                os.path.join(root_dir, "drop.mp3"),
                (0.88, 0.94, 1.0, 1.07, 1.14),
            )
            self.chain_sounds = self._load_pitch_variants(
                os.path.join(root_dir, "chain.mp3"),
                (0.90, 0.96, 1.0, 1.05, 1.11),
            )
            self.enabled = bool(self.drop_sounds or self.chain_sounds)
        except Exception:
            self.enabled = False

    def _load_pitch_variants(self, path: str, pitches) -> list[pygame.mixer.Sound]:
        if not os.path.exists(path):
            return []
        base = pygame.mixer.Sound(path)
        arr = pygame.sndarray.array(base)
        sounds = []
        for pitch in pitches:
            sounds.append(self._pitch_shift(arr, pitch))
        return sounds

    @staticmethod
    def _pitch_shift(arr: np.ndarray, pitch: float) -> pygame.mixer.Sound:
        samples = arr.astype(np.float32)
        source_len = samples.shape[0]
        target_len = max(1, int(source_len / pitch))
        positions = np.linspace(0, source_len - 1, target_len, dtype=np.float32)
        left = np.floor(positions).astype(np.int32)
        right = np.minimum(left + 1, source_len - 1)
        frac = (positions - left).reshape((-1,) + (1,) * (samples.ndim - 1))
        shifted = samples[left] * (1.0 - frac) + samples[right] * frac
        shifted = np.clip(shifted, -32768, 32767).astype(arr.dtype)
        return pygame.sndarray.make_sound(np.ascontiguousarray(shifted))

    @staticmethod
    def _play(sounds, volume: float) -> None:
        if not sounds:
            return
        channel = random.choice(sounds).play()
        if channel:
            channel.set_volume(max(0.0, min(1.0, volume)))

    def update(self, chain_motion: float, impact: float) -> None:
        if not self.enabled:
            return
        now = pygame.time.get_ticks()
        if impact >= config.DROP_IMPACT_MIN and now - self.last_drop_ms >= config.DROP_SOUND_COOLDOWN:
            volume = 0.18 + min(0.72, impact / 15.0)
            self._play(self.drop_sounds, volume)
            self.last_drop_ms = now
        if chain_motion >= config.CHAIN_SOUND_MIN and now - self.last_chain_ms >= config.CHAIN_SOUND_COOLDOWN:
            volume = 0.08 + min(0.34, chain_motion / 18.0)
            self._play(self.chain_sounds, volume)
            self.last_chain_ms = now

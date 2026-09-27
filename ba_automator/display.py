"""The game uses canonical coordinates; ADB and saved evidence use native pixels."""

from __future__ import annotations

from dataclasses import dataclass


CANONICAL_SIZE = (1280, 720)


def is_supported_size(size: tuple[int, int]) -> bool:
    """Bound the capture cost and reject layouts that would require stretching."""
    width, height = size
    return (type(width) is int and type(height) is int
            and 1280 <= width <= 3840 and 720 <= height <= 2160
            and width * 9 == height * 16)


@dataclass(frozen=True)
class GameDisplay:
    width: int
    height: int

    def __post_init__(self):
        if not is_supported_size((self.width, self.height)):
            raise ValueError(
                f"Expected 16:9 landscape from 1280×720 to 3840×2160; "
                f"display is {self.width}×{self.height}"
            )

    def point(self, x: int, y: int) -> tuple[int, int]:
        if (type(x) is not int or type(y) is not int
                or not 0 <= x < 1280 or not 0 <= y < 720):
            raise ValueError("Target must be inside the canonical 1280×720 game frame")
        return round(x * self.width / 1280), round(y * self.height / 720)

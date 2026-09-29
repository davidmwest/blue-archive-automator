"""Pure, bounded greedy planning for rectangular Treasure Hunt boards.

Coordinates are (row, column). This module never taps, refreshes, or spends.
"""
from dataclasses import dataclass
from typing import Mapping

Cell = tuple[int, int]
Placement = frozenset[Cell]


class BoardUncertain(ValueError):
    """Observation is inconsistent or exhaustive validation exceeded its budget."""


@dataclass(frozen=True)
class Treasure:
    id: str
    height: int
    width: int
    rotations: bool = False
    desired: bool = True


@dataclass(frozen=True)
class Choice:
    cell: Cell
    placements_covered: int
    supported_placements: int
    legal_boards: int


def choose_cell(
    rows: int,
    columns: int,
    treasures: tuple[Treasure, ...],
    revealed: Mapping[Cell, str | None],
    *,
    max_nodes: int = 1_000_000,
) -> Choice | None:
    """Maximize distinct feasible desired-treasure placements covered by one click.

    A revealed value is the treasure ID, or None for confirmed empty. Unknown
    OCR must never be supplied as empty. Include completed and unwanted treasures
    because they still occupy space. None means all desired treasures are open.
    Ties prefer more possible immediate completions, then row/column order.
    """
    if rows <= 0 or columns <= 0 or max_nodes <= 0:
        raise ValueError('Board dimensions and search budget must be positive')
    ids = {t.id for t in treasures}
    if len(ids) != len(treasures) or any(not t.id for t in treasures):
        raise ValueError('Treasure IDs must be unique and nonempty')
    board = {(r, c) for r in range(rows) for c in range(columns)}
    if any(cell not in board or (value is not None and value not in ids)
           for cell, value in revealed.items()):
        raise BoardUncertain('Unknown cell or treasure label')
    candidates: dict[str, list[Placement]] = {}
    for t in treasures:
        if t.height <= 0 or t.width <= 0:
            raise ValueError('Treasure dimensions must be positive')
        hits = {cell for cell, value in revealed.items() if value == t.id}
        excluded = {cell for cell, value in revealed.items() if value != t.id}
        orientations = {(t.height, t.width)}
        if t.rotations:
            orientations.add((t.width, t.height))
        placements = set()
        for height, width in sorted(orientations):
            for r in range(rows - height + 1):
                for c in range(columns - width + 1):
                    p = frozenset((y, x) for y in range(r, r + height)
                                  for x in range(c, c + width))
                    if hits <= p and not p & excluded:
                        placements.add(p)
        candidates[t.id] = sorted(placements, key=lambda p: sorted(p))
        if not placements:
            raise BoardUncertain(f'No legal placement for {t.id}')

    # Count each distinct placement once, regardless of how many arrangements
    # the other treasures permit. Enforce global non-overlap before scoring.
    order = sorted(ids, key=lambda name: (len(candidates[name]), name))
    supported: dict[str, set[Placement]] = {name: set() for name in ids}
    nodes = worlds = 0

    def visit(index: int, occupied: Placement, selected: list[Placement]):
        nonlocal nodes, worlds
        nodes += 1
        if nodes > max_nodes:
            raise BoardUncertain('Placement search budget exceeded; no click chosen')
        if index == len(order):
            worlds += 1
            for name, placement in zip(order, selected):
                supported[name].add(placement)
            return
        for placement in candidates[order[index]]:
            if not occupied & placement:
                visit(index + 1, occupied | placement, selected + [placement])

    visit(0, frozenset(), [])
    if not worlds:
        raise BoardUncertain('Treasures cannot fit without overlapping')
    unopened = board - revealed.keys()
    targets = [p for t in treasures if t.desired for p in supported[t.id]
               if p & unopened]
    if not targets:
        return None
    scores = {cell: (sum(cell in p for p in targets),
                     sum(p & unopened == {cell} for p in targets))
              for cell in unopened}
    cell = min(scores, key=lambda c: (-scores[c][0], -scores[c][1], c))
    return Choice(cell, scores[cell][0], len(targets), worlds)

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


@dataclass(frozen=True)
class TreasureShape:
    """Count of one rectangular shape still to finish; completed prizes removed."""

    height: int
    width: int
    count: int
    rotations: bool = True


@dataclass(frozen=True)
class GreedyScore:
    placements_covered: int
    hit_placements_covered: int
    possible_completions: int


@dataclass(frozen=True)
class GreedyChoice:
    cell: Cell
    scores: Mapping[Cell, GreedyScore]
    candidate_count: int


def choose_greedy_cell(
    rows: int,
    columns: int,
    shapes: tuple[TreasureShape, ...],
    *,
    empty=(),
    hits=(),
    completed=(),
    max_placements: int = 10_000,
) -> GreedyChoice | None:
    """Choose from a bounded local coverage heuristic for anonymous treasure hits.

    ``empty`` contains confirmed misses, ``hits`` contains revealed occupied cells
    of unfinished prizes, and ``completed`` contains every cell of finished
    prizes. They must be disjoint. Remove finished prizes from ``shapes`` too.
    Unknown observations must not be supplied as misses. None means the supplied
    inventory is empty, not that an uncertain board was solved.

    Enumerate each shape's locally feasible rectangles, excluding misses and
    finished prizes. Anonymous adjacent hits may belong to different prizes, so
    we do not force a connected group of hits into a single rectangle. Weight
    each placement by the remaining count of its shape. Prefer possible one-click
    completions, then coverage of placements touching existing hits, then total
    placement coverage. Ties use row/column order.

    This is deliberately a heuristic: candidates are not validated against all
    globally nonoverlapping arrangements, and scores are not probabilities or
    an optimality guarantee. Its work is bounded by the rectangle budget rather
    than the exponential number of full-board arrangements. Counts and hit
    coverage checks reject obvious contradictions, but not every impossible
    arrangement. The caller must verify each actual reveal before another move.
    """
    if any(type(value) is not int or value <= 0
           for value in (rows, columns, max_placements)):
        raise ValueError('Board dimensions and placement budget must be positive integers')
    # Bound allocation too, before constructing a board supplied by a caller.
    if rows * columns > max_placements:
        raise BoardUncertain('Board exceeds the greedy placement budget')
    board = {(r, c) for r in range(rows) for c in range(columns)}
    empty, hits, completed = frozenset(empty), frozenset(hits), frozenset(completed)
    if not (empty | hits | completed) <= board:
        raise BoardUncertain('Unknown board cell')
    if empty & hits or empty & completed or hits & completed:
        raise BoardUncertain('A board cell has contradictory observations')
    for shape in shapes:
        if any(type(value) is not int or value < 0
               for value in (shape.height, shape.width, shape.count)):
            raise ValueError('Shape dimensions and count must be nonnegative integers')
        if not shape.height or not shape.width or type(shape.rotations) is not bool:
            raise ValueError('Shape dimensions must be positive and rotations boolean')
    active = [shape for shape in shapes if shape.count]
    if not active:
        if hits:
            raise BoardUncertain('Unfinished hits remain with no remaining treasures')
        return None
    blocked = empty | completed
    unopened = board - blocked - hits
    area = sum(shape.height * shape.width * shape.count for shape in active)
    if len(hits) > area or area > len(board - blocked):
        raise BoardUncertain('Remaining treasure area disagrees with the board')
    candidates = []
    examined = 0
    hit_support = set()
    for shape in active:
        orientations = {(shape.height, shape.width)}
        if shape.rotations:
            orientations.add((shape.width, shape.height))
        placements = set()
        for height, width in sorted(orientations):
            for r in range(rows - height + 1):
                for c in range(columns - width + 1):
                    examined += 1
                    if examined > max_placements:
                        raise BoardUncertain('Greedy placement budget exceeded; no click chosen')
                    placement = frozenset((y, x) for y in range(r, r + height)
                                          for x in range(c, c + width))
                    if not placement & blocked and placement & unopened:
                        placements.add(placement)
        if len(placements) < shape.count:
            raise BoardUncertain('Too few local placements for the remaining shape count')
        for placement in sorted(placements, key=lambda p: sorted(p)):
            candidates.append((placement, shape.count))
            hit_support.update(placement & hits)
    if not hits <= hit_support:
        raise BoardUncertain('An unfinished hit has no possible remaining shape')
    scores = {}
    for cell in sorted(unopened):
        covered = [(placement, count) for placement, count in candidates if cell in placement]
        if covered:
            scores[cell] = GreedyScore(
                placements_covered=sum(count for _, count in covered),
                hit_placements_covered=sum(count for placement, count in covered if placement & hits),
                possible_completions=sum(count for placement, count in covered
                                         if placement & unopened == {cell}),
            )
    if not scores:
        raise BoardUncertain('No unopened cell can complete the remaining treasures')
    chosen = min(scores, key=lambda cell: (
        -scores[cell].possible_completions,
        -scores[cell].hit_placements_covered,
        -scores[cell].placements_covered,
        cell,
    ))
    return GreedyChoice(chosen, scores, len(candidates))

"""Reviewed Aquatic Showdown layouts, in left-to-right supply-card order.

Dimensions/counts: Blue Archive Wiki, Honorable Sea Showdown rerun, Treasure
Hunt tables (linked in docs/event-farming.md). Rounds 1 and 2 verified live.
"""
from .treasure_policy import TreasureShape


def round_shapes(round_no: int) -> tuple[TreasureShape, ...]:
    if type(round_no) is not int or round_no < 1:
        raise ValueError('Invalid treasure round')
    if round_no >= 7:
        values = ((2, 4, 2), (1, 3, 3), (1, 2, 6))
    else:
        values = (
            ((2, 3, 2), (1, 3, 5), (1, 2, 2)),
            ((2, 4, 1), (1, 4, 2), (1, 3, 5)),
            ((3, 3, 1), (2, 2, 4), (1, 2, 3)),
        )[(round_no - 1) % 3]
    return tuple(TreasureShape(*value) for value in values)


def round_counts(round_no: int) -> tuple[int, ...]:
    return tuple(shape.count for shape in round_shapes(round_no))

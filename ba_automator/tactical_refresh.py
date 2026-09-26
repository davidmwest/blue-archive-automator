"""Model-based refresh budgets for a stable, uniformly sampled ladder.

Each row is modeled as an independent sequence of uniform draws from a fixed
finite pool of rank positions. Rows need not be independent of each other.
This is an explicit approximation to the game's undocumented matchmaking, not
a guarantee that all opponents have been discovered. Rank movement invalidates
the model; unreadable rows must not become selective samples.

At predetermined checkpoints we invert the exact occupancy CDF to bound each
pool. The CDF follows the occupied-box Markov chain; see O'Neill, "Three
Distributions in the Extended Occupancy Problem", https://arxiv.org/abs/2209.02220.
For U possible positions and k already observed, the chance of missing any
after m further draws is at most (U-k)*(1-1/U)**m by a union bound. See Cambridge
Randomised Algorithms, lecture 1, https://www.cl.cam.ac.uk/teaching/2122/RandAlgthm/lec1_intro.pdf.

Half the error budget covers all fitted pool bounds, a quarter covers all
coverage stopping certificates, and a quarter covers one subsequent target
lookup. Splitting across the fixed checkpoints avoids optional-stopping error
from repeatedly fitting until a convenient estimate appears.
"""

from dataclasses import dataclass
import math


MAX_POOL_SIZE = 10**12
DEFAULT_CONFIDENCE = .99
MIN_CONFIDENCE = .80
MAX_CONFIDENCE = .999
MAX_SAVED_DRAWS = 1000


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"Expected an integer of at least {minimum}")


def _probability(value):
    if type(value) not in (int, float) or not 0 < value < 1:
        raise ValueError("A probability strictly between zero and one is required")


def _logadd(left, right):
    if left < right:
        left, right = right, left
    return left if right == -math.inf else left + math.log1p(math.exp(right - left))


def _log_stirling(draws, maximum):
    """Log S(draws,j), retaining the exact occupancy recurrence in float math."""
    values = [0.0] + [-math.inf] * maximum
    logs = [0.0] + [math.log(j) for j in range(1, maximum + 1)]
    for count in range(1, draws + 1):
        for distinct in range(min(count, maximum), 0, -1):
            values[distinct] = _logadd(values[distinct] + logs[distinct], values[distinct - 1])
        values[0] = -math.inf
    return values


def _occupancy_cdf(pool, draws, distinct, stirling):
    if distinct >= min(pool, draws):
        return 1.0
    total, falling = -math.inf, 0.0
    denominator = draws * math.log(pool)
    for occupied in range(1, distinct + 1):
        falling += math.log(pool - occupied + 1)
        total = _logadd(total, falling + stirling[occupied] - denominator)
    return min(1.0, math.exp(total))


def occupancy_cdf(pool, draws, distinct):
    """P(K <= distinct) for draws uniform samples from pool labeled positions."""
    _integer(pool, 1)
    _integer(draws)
    _integer(distinct)
    if draws == 0:
        return 1.0
    if distinct == 0:
        return 0.0
    if distinct >= min(pool, draws):
        return 1.0
    return _occupancy_cdf(pool, draws, distinct, _log_stirling(draws, distinct))


def _pool_upper(draws, distinct, alpha, stirling):
    if distinct == draws:
        return None  # No collisions: arbitrarily large pools fit the sample.
    def accepted(pool):
        cdf = _occupancy_cdf(pool, draws, distinct, stirling)
        # Round an exact boundary toward a larger (conservative) pool, never
        # reject it because log-space accumulation lost a few ulps.
        return cdf >= alpha or math.isclose(cdf, alpha, rel_tol=1e-12)
    lower, upper = distinct, max(distinct + 1, 2 * distinct)
    while accepted(upper):
        lower = upper
        if upper >= MAX_POOL_SIZE:
            return None
        upper = min(MAX_POOL_SIZE, upper * 2)
    while lower + 1 < upper:
        middle = (lower + upper) // 2
        if accepted(middle):
            lower = middle
        else:
            upper = middle
    return lower


def pool_upper_bound(draws, distinct, alpha=.01):
    """One-sided (1-alpha) upper bound; None means no supported finite bound."""
    _integer(draws, 1)
    _integer(distinct, 1)
    _probability(alpha)
    if distinct > draws:
        raise ValueError("Distinct positions cannot exceed observed draws")
    return _pool_upper(draws, distinct, alpha, _log_stirling(draws, distinct))


def _pool_mle(draws, distinct, upper):
    if upper is None:
        return None
    lower = distinct
    while lower < upper:
        middle = (lower + upper) // 2
        # log L(N+1)/L(N); the occupancy Stirling factor cancels.
        ratio = -math.log1p(-distinct / (middle + 1)) + draws * math.log1p(-1 / (middle + 1))
        if ratio > 0:
            lower = middle + 1
        else:
            upper = middle
    return lower


def coverage_draws(pool, already_seen, failure_probability):
    """Further draws for a union-bound certificate; never subtract pilot draws."""
    _integer(pool, 1)
    _integer(already_seen)
    _probability(failure_probability)
    if already_seen > pool:
        raise ValueError("Observed positions cannot exceed the pool bound")
    unseen = pool - already_seen
    if unseen == 0:
        return 0
    if pool == 1:
        return 1
    return math.ceil((math.log(failure_probability) - math.log(unseen)) / math.log1p(-1 / pool))


def lookup_draws(pool, failure_probability=.01):
    """Draws needed to see a fixed member of a uniform pool with high probability."""
    _integer(pool, 1)
    _probability(failure_probability)
    if pool == 1:
        return 1
    return math.ceil(math.log(failure_probability) / math.log1p(-1 / pool))


@dataclass(frozen=True)
class RowEstimate:
    row: int
    draws: int
    distinct: int
    pool_estimate: int | None
    pool_upper: int | None


@dataclass(frozen=True)
class RefreshPlan:
    status: str
    physical_refreshes: int
    valid_draws: int
    row_estimates: tuple[RowEstimate, ...]
    target_valid_draws: int | None
    confidence: float
    reason: str


@dataclass(frozen=True)
class LookupPlan:
    required_refreshes: int | None
    row: int | None
    within_limit: bool
    confidence: float
    reason: str


class RefreshPlanner:
    """Bounded survey planner; call observe once per actual list refresh.

    Supply all three independently recognized rank labels even when team OCR
    is incomplete. A missing, invalid, or duplicate rank rejects the whole
    draw; usable captures must not favor particular rank positions. Initial
    menus and repeated screenshots of one refresh are not draws.
    Use a new planner when player rank/day changes or target lookup expires.
    """

    def __init__(self, *, pilot=50, max_refreshes=1000, confidence=DEFAULT_CONFIDENCE):
        _integer(pilot, 2)
        _integer(max_refreshes, pilot)
        if max_refreshes > MAX_SAVED_DRAWS:
            raise ValueError("Refresh surveys support at most 1000 draws")
        _probability(confidence)
        if not MIN_CONFIDENCE <= confidence <= MAX_CONFIDENCE:
            raise ValueError("Refresh confidence must be between 0.80 and 0.999 inclusive")
        self.pilot, self.max_refreshes, self.confidence = pilot, max_refreshes, confidence
        checkpoints = []
        checkpoint = pilot
        while checkpoint < max_refreshes:
            checkpoints.append(checkpoint)
            checkpoint *= 2
        checkpoints.append(max_refreshes)
        self.checkpoints = tuple(checkpoints)
        self.physical_refreshes = self.valid_draws = 0
        self._seen = [set(), set(), set()]
        self._estimates = ()
        self._target = None
        self._complete = False
        self._draws = []

    def to_dict(self):
        """Serialize observations, never a cached confidence/stopping claim."""
        if self.max_refreshes > MAX_SAVED_DRAWS:
            raise ValueError("Persisted refresh surveys support at most 1000 draws")
        return {"version": 1, "pilot": self.pilot, "max_refreshes": self.max_refreshes,
                "confidence": self.confidence,
                "draws": [list(draw) if draw is not None else None for draw in self._draws]}

    @classmethod
    def from_dict(cls, value):
        """Replay a bounded history to reproduce every fitted certificate."""
        keys = {"version", "pilot", "max_refreshes", "confidence", "draws"}
        if (not isinstance(value, dict) or set(value) != keys
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError("Invalid saved refresh survey")
        if (type(value["max_refreshes"]) is not int
                or not 2 <= value["max_refreshes"] <= MAX_SAVED_DRAWS
                or not isinstance(value["draws"], list)
                or len(value["draws"]) > value["max_refreshes"]):
            raise ValueError("Saved refresh history exceeds its bound")
        planner = cls(pilot=value["pilot"], max_refreshes=value["max_refreshes"],
                      confidence=value["confidence"])
        for draw in value["draws"]:
            if draw is not None and not (
                    isinstance(draw, list) and len(draw) == 3
                    and all(type(rank) is int and rank > 0 for rank in draw)
                    and len(set(draw)) == 3):
                raise ValueError("Saved refresh ranks must be three distinct observed integers")
            planner.observe(draw)
        return planner

    def _fit(self):
        alpha = (1 - self.confidence) / (2 * 3 * len(self.checkpoints))
        coverage_alpha = (1 - self.confidence) / (4 * 3 * len(self.checkpoints))
        counts = [len(row) for row in self._seen]
        stirling = _log_stirling(self.valid_draws, max(counts))
        estimates = tuple(
            RowEstimate(row, self.valid_draws, count,
                        _pool_mle(self.valid_draws, count, upper), upper)
            for row, count in enumerate(counts)
            for upper in [_pool_upper(self.valid_draws, count, alpha, stirling)]
        )
        if any(row.pool_upper is None for row in estimates):
            if self._target is None:
                self._estimates = estimates
            return
        target = self.valid_draws + max(
            coverage_draws(row.pool_upper, row.distinct, coverage_alpha) for row in estimates)
        # Keep the earliest valid certificate; all fitted certificates share
        # the preallocated error budget, so selecting between them is valid.
        if self._target is None or target < self._target:
            self._estimates, self._target = estimates, target

    def observe(self, ranks):
        if self._complete or self.physical_refreshes >= self.max_refreshes:
            raise ValueError("The refresh survey is finished; start a new planner")
        self.physical_refreshes += 1
        valid = (isinstance(ranks, (tuple, list)) and len(ranks) == 3
                 and all(type(rank) is int and rank > 0 for rank in ranks)
                 and len(set(ranks)) == 3)
        self._draws.append(tuple(ranks) if valid else None)
        if valid:
            self.valid_draws += 1
            for row, rank in zip(self._seen, ranks):
                row.add(rank)
            if self._target is not None and any(
                    len(self._seen[row.row]) > row.pool_upper for row in self._estimates):
                # New observations can directly contradict an earlier bound.
                # Do not retain its optimistic stopping certificate; refit
                # only at the next checkpoint with its own error allocation.
                self._target = None
                self._estimates = ()
            if self._target is not None and self.valid_draws >= self._target:
                self._complete = True
            elif self.valid_draws in self.checkpoints:
                self._fit()
                self._complete = self._target is not None and self.valid_draws >= self._target
        return self.plan()

    def plan(self):
        if self._complete:
            status = "complete"
            reason = "Model-based coverage target reached for stable uniform row pools"
        elif self.physical_refreshes >= self.max_refreshes:
            status = "deferred"
            reason = "Refresh cap reached before model-based confidence; no complete survey claimed"
        elif self.valid_draws < self.pilot:
            status = "pilot"
            reason = "Collecting fully read rank lists for the fixed pilot sample"
        else:
            status = "sampling"
            reason = ("No finite pool bound yet; waiting for the next fixed checkpoint"
                      if self._target is None else "Sampling toward the model-based coverage target")
        return RefreshPlan(status, self.physical_refreshes, self.valid_draws,
                           self._estimates, self._target, self.confidence, reason)

    def lookup_plan(self, rank):
        _integer(rank, 1)
        rows = [row for row in self._estimates
                if rank in self._seen[row.row] and row.pool_upper is not None]
        if not self._complete or not rows:
            return LookupPlan(None, None, False, self.confidence,
                              "A completed survey containing the selected rank is required")
        row = min(rows, key=lambda estimate: estimate.pool_upper)
        required = lookup_draws(row.pool_upper, (1 - self.confidence) / 4)
        within = required <= self.max_refreshes
        return LookupPlan(required, row.row, within, self.confidence,
                          "Target absent at this bound suggests a changed ladder; start a fresh survey"
                          if within else "Target confidence requires more refreshes than the safety cap")

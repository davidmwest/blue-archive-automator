"""Timed opponent selection with gradual overtime relaxation.

The first 37% of active search time establishes a reference population. Later
lists may supply a current opponent at or below its gradually relaxed empirical
percentile. This is an operational heuristic, not a secretary-problem guarantee:
the game supplies repeated, discrete, changing opponents rather than a random
permutation. After the configured time, each acknowledged refresh admits the
next observed strength tier. Queue waiting never advances this policy.
"""

from dataclasses import dataclass, field
import math
import random

from .tactical_battles import Opponent, opponent_score


VERSION = 2
BENCHMARK_FRACTION = 0.37
FINAL_PERCENTILE = 0.25
MAX_IDENTITIES = 1000
MAX_OBSERVATIONS = 10000
MIN_BENCHMARK_OBSERVATIONS = 2


def _number(value, label, *, minimum=0, maximum=None):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < minimum or (maximum is not None and value > maximum)):
        raise ValueError(f"Invalid {label}")
    return float(value)


def _count(value, label, *, maximum=MAX_OBSERVATIONS):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"Invalid {label}")
    return value


def _identity(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 128):
        raise ValueError("Invalid opponent identity")
    return value


@dataclass(frozen=True)
class SearchPolicy:
    time_budget_seconds: float = 600

    def __post_init__(self):
        object.__setattr__(self, "time_budget_seconds", _number(
            self.time_budget_seconds, "search time budget", minimum=1, maximum=3600))

    @property
    def benchmark_seconds(self):
        return self.time_budget_seconds * BENCHMARK_FRACTION


@dataclass(frozen=True)
class SearchDecision:
    phase: str
    threshold: float | None
    percentile: float
    opponent_id: str | None
    candidate_ids: tuple[str, ...]
    elapsed_seconds: float
    remaining_seconds: float
    estimated_refreshes: int | None
    can_refresh: bool


@dataclass
class SearchState:
    policy: SearchPolicy = field(default_factory=SearchPolicy)
    elapsed_seconds: float = 0
    refreshes: int = 0
    observations: int = 0
    benchmark_observations: int = 0
    scores: dict[str, float] = field(default_factory=dict)
    benchmark_ids: set[str] = field(default_factory=set)
    initial_observed: bool = False
    overtime_refreshes: int = 0
    overtime_threshold: float | None = None

    def __post_init__(self):
        self._validate()
        self.scores = dict(self.scores)
        self.benchmark_ids = set(self.benchmark_ids)

    def _validate(self):
        if not isinstance(self.policy, SearchPolicy):
            raise ValueError("A search policy is required")
        _number(self.elapsed_seconds, "elapsed search time", maximum=self.policy.time_budget_seconds)
        _count(self.refreshes, "refresh count")
        _count(self.observations, "observation count")
        _count(self.benchmark_observations, "benchmark observation count")
        _count(self.overtime_refreshes, "overtime refresh count")
        if (self.overtime_refreshes > self.refreshes
                or (self.overtime_refreshes and not self.deadline_reached)):
            raise ValueError("Inconsistent overtime refresh count")
        if self.overtime_threshold is not None:
            if (_number(self.overtime_threshold, "overtime threshold") <= 0
                    or not self.overtime_refreshes):
                raise ValueError("An overtime threshold requires an acknowledged refresh")
        if type(self.initial_observed) is not bool:
            raise ValueError("Invalid initial observation flag")
        if not 0 <= self.benchmark_observations <= self.observations <= self.refreshes + int(self.initial_observed):
            raise ValueError("Inconsistent search observation counts")
        if not isinstance(self.scores, dict) or len(self.scores) > MAX_IDENTITIES:
            raise ValueError("Invalid search score population")
        for identity, score in self.scores.items():
            _identity(identity)
            if _number(score, "opponent score") <= 0:
                raise ValueError("An opponent score must be positive")
        if not isinstance(self.benchmark_ids, (set, list, tuple)):
            raise ValueError("Invalid benchmark population")
        for identity in self.benchmark_ids:
            _identity(identity)
        if (len(set(self.benchmark_ids)) != len(self.benchmark_ids)
                or not set(self.benchmark_ids).issubset(self.scores)):
            raise ValueError("Invalid benchmark population")
        if bool(self.benchmark_ids) != bool(self.benchmark_observations):
            raise ValueError("Benchmark observations require a reference population")
        if bool(self.scores) != bool(self.observations):
            raise ValueError("Readable observations require scores")
        if self.overtime_threshold is not None and (
                not self.scores or self.overtime_threshold > max(self.scores.values())):
            raise ValueError("Overtime threshold must come from observed scores")

    @property
    def remaining_seconds(self):
        return max(0.0, self.policy.time_budget_seconds - self.elapsed_seconds)

    @property
    def deadline_reached(self):
        return self.remaining_seconds == 0

    @property
    def exhausted(self):
        """Compatibility alias for the time threshold, not a stop condition."""
        return self.deadline_reached

    @property
    def estimated_refreshes(self):
        within_time = self.refreshes - self.overtime_refreshes
        if not within_time or self.elapsed_seconds <= 0:
            return None
        return math.floor(self.policy.time_budget_seconds * within_time / self.elapsed_seconds)

    def advance(self, seconds):
        """Charge active time until the threshold; overtime advances by refresh."""
        seconds = _number(seconds, "search time increment")
        self.elapsed_seconds = min(self.policy.time_budget_seconds, self.elapsed_seconds + seconds)

    @staticmethod
    def _current(opponents):
        opponents = tuple(opponents)
        if len(opponents) > 3 or any(not isinstance(p, Opponent) for p in opponents):
            raise ValueError("Supply at most three verified current opponents")
        if len({p.opponent_id for p in opponents}) != len(opponents):
            raise ValueError("Current opponent identities must be distinct")
        for opponent in opponents:
            _identity(opponent.opponent_id)
        return opponents

    def observe(self, opponents, *, refreshed=True):
        """Record one initial list or an acknowledged refresh, including blanks.

        The caller filters ranks and previously fought identities and verifies
        visible levels. Empty/unreadable lists consume a draw, but contribute
        no score. Repeated identities have one population entry, retaining the
        highest score observed rather than trusting a later optimistic reading.
        """
        if type(refreshed) is not bool:
            raise ValueError("Refresh acknowledgment must be a boolean")
        opponents = self._current(opponents)
        if not refreshed and self.initial_observed:
            raise ValueError("Only one initial list may be recorded")
        if self.refreshes + int(refreshed) > MAX_OBSERVATIONS:
            raise ValueError("Search observation limit reached")
        if self.observations + bool(opponents) > MAX_OBSERVATIONS:
            raise ValueError("Search observation limit reached")
        new_ids = {p.opponent_id for p in opponents} - self.scores.keys()
        if len(self.scores) + len(new_ids) > MAX_IDENTITIES:
            raise ValueError("Search identity limit reached")
        self.refreshes += int(refreshed)
        if not refreshed:
            self.initial_observed = True
        if opponents:
            self.observations += 1
            benchmarking = self.elapsed_seconds < self.policy.benchmark_seconds
            if benchmarking:
                self.benchmark_observations += 1
            for opponent in opponents:
                identity = opponent.opponent_id
                score = opponent_score(opponent)
                self.scores[identity] = max(self.scores.get(identity, score), score)
                if benchmarking:
                    self.benchmark_ids.add(identity)
        if refreshed and self.deadline_reached:
            self.overtime_refreshes += 1
            self._advance_overtime()

    def _benchmark_threshold(self, percentile):
        if self.benchmark_observations < MIN_BENCHMARK_OBSERVATIONS:
            return None
        reference = sorted(self.scores[key] for key in self.benchmark_ids)
        return reference[math.floor(percentile * (len(reference) - 1))]

    def _advance_overtime(self):
        if not self.scores:
            return
        baseline = self._benchmark_threshold(FINAL_PERCENTILE)
        if baseline is None:
            # No useful early benchmark: overtime still requires observed,
            # caller-verified eligible opponents before setting a threshold.
            baseline = min(self.scores.values())
        floor = max(baseline, self.overtime_threshold or baseline)
        higher = sorted({score for score in self.scores.values() if score > floor})
        self.overtime_threshold = higher[0] if higher else floor

    def decide(self, current_opponents, *, rng=None):
        """Choose only from the current list, including admitted overtime tiers.

        The floor-index percentile is conservative for small populations and
        never invents a threshold between observed scores. Equality qualifies
        because the same good opponent may reappear. Exact weakest-score ties
        are randomized only among currently visible qualifying opponents.
        """
        current = self._current(current_opponents)
        benchmarking = self.elapsed_seconds < self.policy.benchmark_seconds
        progress = max(0.0, (self.elapsed_seconds / self.policy.time_budget_seconds
                             - BENCHMARK_FRACTION) / (1 - BENCHMARK_FRACTION))
        percentile = min(FINAL_PERCENTILE, progress * FINAL_PERCENTILE)
        threshold = self._benchmark_threshold(percentile)
        if self.deadline_reached and self.overtime_threshold is not None:
            threshold = max(threshold or self.overtime_threshold, self.overtime_threshold)
        candidates = []
        if not benchmarking and threshold is not None:
            # A current reading may only worsen its own previous score; this
            # method does not alter the sampled reference or observation count.
            scored = [(max(opponent_score(p), self.scores.get(p.opponent_id, 0)), p.opponent_id)
                      for p in current]
            qualified = [(score, identity) for score, identity in scored if score <= threshold]
            if qualified:
                best = min(score for score, _ in qualified)
                candidates = [identity for score, identity in qualified if score == best]
        selected = (rng or random.SystemRandom()).choice(candidates) if candidates else None
        phase = ("accepted" if selected is not None else "overtime" if self.deadline_reached
                 else "benchmark" if benchmarking else "search")
        return SearchDecision(phase, threshold, percentile, selected, tuple(candidates),
                              self.elapsed_seconds, self.remaining_seconds,
                              self.estimated_refreshes, selected is None)

    def to_dict(self):
        self._validate()
        return {"version": VERSION,
                "policy": {"time_budget_seconds": self.policy.time_budget_seconds},
                "elapsed_seconds": self.elapsed_seconds, "refreshes": self.refreshes,
                "observations": self.observations,
                "benchmark_observations": self.benchmark_observations,
                "scores": dict(self.scores), "benchmark_ids": sorted(self.benchmark_ids),
                "initial_observed": self.initial_observed,
                "overtime_refreshes": self.overtime_refreshes,
                "overtime_threshold": self.overtime_threshold}

    @classmethod
    def from_dict(cls, value):
        keys = {"version", "policy", "elapsed_seconds", "refreshes", "observations",
                "benchmark_observations", "scores", "benchmark_ids", "initial_observed",
                "overtime_refreshes", "overtime_threshold"}
        if isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1:
            legacy_keys = keys - {"overtime_refreshes", "overtime_threshold"}
            if set(value) != legacy_keys:
                raise ValueError("Unsupported legacy opponent search state")
            value = dict(value, version=VERSION, overtime_refreshes=0, overtime_threshold=None)
        if not isinstance(value, dict) or set(value) != keys or type(value["version"]) is not int or value["version"] != VERSION:
            raise ValueError("Unsupported opponent search state")
        policy = value["policy"]
        if not isinstance(policy, dict) or set(policy) != {"time_budget_seconds"}:
            raise ValueError("Invalid saved search policy")
        if not isinstance(value["benchmark_ids"], list):
            raise ValueError("Invalid saved benchmark population")
        return cls(SearchPolicy(**policy), **{key: value[key] for key in keys - {"version", "policy"}})

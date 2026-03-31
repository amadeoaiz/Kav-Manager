from dataclasses import dataclass, field


@dataclass
class LPWeights:
    """All tunable weights and constraint parameters for the two-stage block LP solver."""

    # Day weights
    w_day_fairness: float = 50.0
    w_day_minimax: float = 30.0
    w_day_fragmentation: float = 2.0
    w_day_points: float = 1.0

    # Night weights
    w_night_wakeups: float = 5.0
    w_night_fairness: float = 25.0
    w_night_minimax: float = 15.0
    w_night_hardship: float = 3.0
    w_night_points: float = 1.5

    # Within-segment proximity penalty (unified: replaces old stretch + rest gap)
    # Monotonically decreasing: gap=0h (adjacent) = max cost, cost→0 at threshold.
    # Day threshold ~5h, night threshold ~6h.
    w_day_proximity: float = 12.0      # Within-segment proximity cost (day)
    w_night_proximity: float = 20.0    # Within-segment proximity cost (night — sleep matters more)

    # Cross-domain rest gap penalties (between segments, unchanged)
    w_rest_day_night: float = 20.0  # day→night transition penalty
    w_rest_night_day: float = 20.0  # night→day transition penalty

    # Frozen/fixed → block gap penalty (higher than proximity to outweigh fairness)
    w_rest_frozen: float = 25.0

    # Coverage slack penalty (dominates all others)
    w_coverage: float = 1000.0

    # Max night starts (hard cap on wakeups)
    max_night_starts: int = 2

    # Fairness balance
    alpha: float = 0.7    # average deviation weight
    beta: float = 0.3     # minimax weight

    # Block generation parameters
    target_block_lengths: list[int] = field(
        default_factory=lambda: [60, 75, 90, 105, 120, 150, 180],
    )
    min_block_minutes: int = 30
    max_block_minutes: int = 180

    # Fixed-task penalties
    w_fixed_stack: float = 5.0     # same-day fixed-task stacking penalty
    w_fixed_excess: float = 1.0    # excess-aware cost for fixed task assignments

    # Decay scoring
    decay_rate: float = 0.85
    lookback_days: int = 14

    # --- Block length sweet spot ---
    # Configs with target block length ≤ sweet_spot_max_minutes incur no penalty.
    # Above that, penalty grows quadratically: w × ((target - max) / 30)^exp
    # With w=200, exp=2: 105min→56, 120min→200, 150min→800, 180min→1800
    # Tuned so 105min wins most cycles; 120min can still win when genuinely better.
    # Higher values (225+) shut out 120min entirely and create sub-3h rest gaps.
    sweet_spot_max_minutes: float = 90.0
    w_long_block_penalty: float = 200.0   # Base weight for the quadratic penalty
    long_block_exponent: float = 2.0       # Growth rate (2.0 = quadratic)

    # Solver parameters
    ratio_gap: float = 0.03
    time_limit_seconds: int = 1

"""Shapley decomposition, dual-level (v0.2 step 2).

[想象含量 L1][证据锚: §4.4-表4.3 特征级 Shapley 对照]
[想象含量 L1][证据锚: §4.6-表4.10 门店级 Shapley]
[想象含量 L1][证据锚: §4.8.1-局限1 n>20 需近似算法]

Two algorithms, CALLER-EXPLICIT (no auto-switch):
  Paper L303 (§4.8.1 局限1) discloses the O(2^n) bottleneck for n > 20 and
  names Monte Carlo Shapley as the remedy -- it does NOT define an automatic
  switching mechanism. The caller must pass algorithm explicitly:
    - "exact":       subset enumeration, O(2^n). Hard cap n <= 20;
                     n > 20 raises ValueError (opt into monte_carlo instead).
    - "monte_carlo": random-permutation sampling; returns a per-player
                     sampling variance estimate across permutations.

Feature level (表4.3 semantics):
  Value function per sample x: v(S | x) = tau_hat(x with features not in S
  set to baseline), baseline = all-zeros (the generator's natural zero:
  tau(base) = store base intercept). Efficiency axiom is hard-checked
  per sample: max_x |Σ_k φ_k(x) − (τ̂(x) − τ̂(base))| ~ machine epsilon
  (paper reports 1.759e-13 on their pipeline).

  Scale note (§5 honesty boundary): the paper's 表4.3 真实φ均值 are the
  generator's structural coefficients (TRUE_PHI). Under the SAME value
  function + zero baseline, the closed-form truth-side Shapley mean is
  φ_k · E[x_k]. We report BOTH scales separately:
    - true_phi_structural      : TRUE_PHI (paper 表4.3 scale)
    - true_phi_means_same_scale: φ_k·E[x_k] (same ruler as model side)
  Attribution errors are computed on the SAME-SCALE pair only.

Store level (表4.10 semantics, alliance-formation value function):
  v(S) = Σ τ̂(x) over samples whose home_store AND item_store are both in S
  (an increment flow counts only when both ends are inside the alliance S).
  This reproduces the cross-store flow interaction of 表4.10 without the
  Stackelberg λ* equilibrium module.
  BOUNDARY (user-established argumentation limit): paper 表4.10 is the
  λ*=1.00 degenerate solution and must NOT be cited as evidence that the
  transfer-payment mechanism works standalone. Our store-level φ is an
  alliance-formation attribution on synthetic flows, not the equilibrium
  transfer table.
"""
from __future__ import annotations

from collections.abc import Callable
from math import factorial

import numpy as np

MAX_EXACT_PLAYERS = 20  # paper L303: O(2^n) bottleneck beyond 20 players


# ---------------------------------------------------------------------------
# generic exact Shapley from a value table indexed by bitmask
# ---------------------------------------------------------------------------

def _subset_weights(n: int) -> np.ndarray:
    """w[m] = m!·(n−m−1)!/n! for a subset of size m (m = 0..n).

    Length n+1 so the full-set size n is indexable; w[n] is never used by
    the Shapley formula (phi_i only sums over subsets not containing i)
    and is set to 0.
    """
    w = np.zeros(n + 1, dtype=np.float64)
    for m in range(n):
        w[m] = factorial(m) * factorial(n - m - 1) / factorial(n)
    return w


def shapley_exact_by_mask(n: int, v_by_mask: np.ndarray) -> np.ndarray:
    """Exact Shapley values from v indexed by bitmask (player i = bit i).

    φ_i = Σ_{S⊆N\\{i}} w(|S|)·[v(S∪{i}) − v(S)]

    Raises ValueError for n > 20 (caller must use monte_carlo).
    """
    if n > MAX_EXACT_PLAYERS:
        raise ValueError(
            f"exact Shapley requires n <= {MAX_EXACT_PLAYERS} (2^n subset "
            f"enumeration); got n={n}. Use algorithm='monte_carlo'."
        )
    n_sub = 1 << n
    if len(v_by_mask) != n_sub:
        raise ValueError(f"v_by_mask must have 2^n={n_sub} entries, got {len(v_by_mask)}")
    v = np.asarray(v_by_mask, dtype=np.float64)
    sizes = np.array([bin(s).count("1") for s in range(n_sub)])
    w_all = np.zeros(n_sub, dtype=np.float64)
    w_all[:] = _subset_weights(n)[sizes]

    phi = np.zeros(n, dtype=np.float64)
    for i in range(n):
        idx = np.array([s for s in range(n_sub) if not (s >> i) & 1], dtype=np.int64)
        phi[i] = float(np.sum(w_all[idx] * (v[idx | (1 << i)] - v[idx])))
    return phi


def shapley_monte_carlo(
    n: int,
    v_fn: Callable[[frozenset[int]], float],
    n_permutations: int = 100,
    seed: int = 20240914,
) -> tuple[np.ndarray, np.ndarray]:
    """Monte Carlo Shapley via random permutations.

    Returns (phi_means, sampling_variance) where sampling_variance is the
    per-player variance of the per-permutation marginal-contribution
    estimates (ddof=1). Each permutation's φ estimates telescope to
    v(N) − v(∅) exactly; variance reflects ordering sensitivity only.
    """
    if n_permutations < 2:
        raise ValueError("n_permutations must be >= 2 for a variance estimate")
    rng = np.random.default_rng(seed)
    players = list(range(n))
    phi_perm = np.empty((n_permutations, n), dtype=np.float64)
    for p in range(n_permutations):
        perm = rng.permutation(players)
        S: frozenset[int] = frozenset()
        cur = v_fn(S)
        for i in perm:
            S = S | {i}
            new = v_fn(S)
            phi_perm[p, i] = new - cur
            cur = new
    phi = phi_perm.mean(axis=0)
    var = phi_perm.var(axis=0, ddof=1)
    return phi, var


# ---------------------------------------------------------------------------
# feature level (表4.3): per-sample Shapley of tau_hat over 10 features
# ---------------------------------------------------------------------------

def feature_shapley_exact(
    predict_fn: Callable[[np.ndarray], np.ndarray],
    X: np.ndarray,
    baseline: np.ndarray | None = None,
    fixed_features: list[int] | None = None,
) -> dict[str, np.ndarray | float]:
    """Per-sample exact feature Shapley (batched over all 2^d subsets).

    predict_fn(M) -> (n,) τ̂ values for a masked matrix M.
    baseline: per-feature reference values; default zeros.
    fixed_features: indices held at their OBSERVED value in every masked
      variant (conditional attribution). A fixed feature's φ is exactly 0:
      the between-segment level it carries belongs to segment-level
      decompositions (表4.4 / 表4.10), not feature attribution. Our
      equivalent generator realizes store level via 门店等级 ≡ 6−home_store
      (a deterministic proxy), so the store-tier feature MUST be fixed to
      keep 表4.3 semantics (门店等级 真实φ=0). Design interpretation under
      §5 -- the paper's exact value function is not recoverable from the
      disclosed text (source unavailable).

    Returns {phi_means, per_sample_phi, v_base, efficiency_max_residual}.
    Raises ValueError for d > 20.
    """
    X = np.asarray(X, dtype=np.float64)
    n, d = X.shape
    if d > MAX_EXACT_PLAYERS:
        raise ValueError(
            f"exact feature Shapley requires d <= {MAX_EXACT_PLAYERS}; got d={d}. "
            "Use feature_shapley_monte_carlo."
        )
    if baseline is None:
        baseline = np.zeros(d, dtype=np.float64)
    baseline = np.asarray(baseline, dtype=np.float64)
    fixed = np.zeros(d, dtype=bool)
    if fixed_features:
        fixed[np.asarray(fixed_features, dtype=np.int64)] = True

    n_sub = 1 << d
    # per-sample reference predictions: ALL free features at baseline,
    # fixed features at observed (handles per-sample intercepts too)
    M0 = np.tile(baseline, (n, 1))
    M0[:, fixed] = X[:, fixed]
    v_base_vec = predict_fn(M0)

    V = np.empty((n, n_sub), dtype=np.float64)
    for s in range(n_sub):
        mask = np.array([(s >> k) & 1 for k in range(d)], dtype=bool)
        M = X.copy()
        M[:, ~mask & ~fixed] = baseline[~mask & ~fixed]
        V[:, s] = predict_fn(M)

    sizes = np.array([bin(s).count("1") for s in range(n_sub)])
    w_all = _subset_weights(d)[sizes]

    per_sample = np.empty((n, d), dtype=np.float64)
    for i in range(d):
        idx = np.array([s for s in range(n_sub) if not (s >> i) & 1], dtype=np.int64)
        partner = idx | (1 << i)
        per_sample[:, i] = np.sum(
            (V[:, partner] - V[:, idx]) * w_all[idx][None, :], axis=1
        )

    # efficiency axiom per sample: Σ_k φ_k(x) = τ̂(x) − τ̂(base)
    tau_hat = predict_fn(X)
    residual = float(np.max(np.abs(per_sample.sum(axis=1) - (tau_hat - v_base_vec))))

    return {
        "phi_means": per_sample.mean(axis=0),
        "per_sample_phi": per_sample,
        "v_base": float(v_base_vec.mean()),
        "efficiency_max_residual": residual,
    }


def feature_shapley_monte_carlo(
    predict_fn: Callable[[np.ndarray], np.ndarray],
    X: np.ndarray,
    n_permutations: int = 100,
    seed: int = 20240914,
    baseline: np.ndarray | None = None,
    fixed_features: list[int] | None = None,
) -> dict[str, np.ndarray | float]:
    """Mass-level Monte Carlo feature Shapley on the mean value function.

    v(S) = mean_x τ̂(x with free features not in S at baseline; fixed
    features always at observed). Each permutation telescopes exactly to
    v(N) − v(∅); per-player variance across permutations is the sampling
    variance estimate. See feature_shapley_exact for the fixed_features
    rationale.
    """
    X = np.asarray(X, dtype=np.float64)
    n, d = X.shape
    if baseline is None:
        baseline = np.zeros(d, dtype=np.float64)
    baseline = np.asarray(baseline, dtype=np.float64)
    fixed = np.zeros(d, dtype=bool)
    if fixed_features:
        fixed[np.asarray(fixed_features, dtype=np.int64)] = True
    if n_permutations < 2:
        raise ValueError("n_permutations must be >= 2 for a variance estimate")

    rng = np.random.default_rng(seed)
    M0 = np.tile(baseline, (n, 1))
    M0[:, fixed] = X[:, fixed]
    v_base_vec = predict_fn(M0)
    v_base = float(v_base_vec.mean())
    phi_perm = np.empty((n_permutations, d), dtype=np.float64)
    for p in range(n_permutations):
        perm = rng.permutation(d)
        S = np.zeros(d, dtype=bool)
        cur = v_base_vec.copy()
        for i in perm:
            S[i] = True
            M = X.copy()
            M[:, ~S & ~fixed] = baseline[~S & ~fixed]
            new = predict_fn(M)
            phi_perm[p, i] = float((new - cur).mean())
            cur = new

    residual = float(
        abs(phi_perm.mean(axis=0).sum() - (float(predict_fn(X).mean()) - v_base))
    )
    return {
        "phi_means": phi_perm.mean(axis=0),
        "sampling_variance": phi_perm.var(axis=0, ddof=1),
        "v_base": v_base,
        "efficiency_max_residual": residual,
    }


# ---------------------------------------------------------------------------
# store level (表4.10): alliance-formation value function over 6 stores
# ---------------------------------------------------------------------------

def _store_value(
    tau_hat: np.ndarray,
    home_store: np.ndarray,
    item_store: np.ndarray,
    subset: frozenset[int] | set[int],
) -> float:
    """v(S) = Σ τ̂(x) over samples with BOTH home_store and item_store in S."""
    if not subset:
        return 0.0
    S = np.fromiter(subset, dtype=np.int64)
    in_home = np.isin(home_store, S)
    in_item = np.isin(item_store, S)
    return float(tau_hat[in_home & in_item].sum())


def store_shapley_exact(
    tau_hat: np.ndarray,
    home_store: np.ndarray,
    item_store: np.ndarray,
    n_stores: int,
) -> dict[str, np.ndarray | float]:
    """Exact store-level Shapley (6 players → 2^6 = 64 subsets).

    Returns {phi, v_n, efficiency_max_residual} where residual is
    |Σφ − v(N)| (efficiency axiom; v(∅)=0).
    """
    tau_hat = np.asarray(tau_hat, dtype=np.float64)
    home_store = np.asarray(home_store, dtype=np.int64)
    item_store = np.asarray(item_store, dtype=np.int64)
    if n_stores > MAX_EXACT_PLAYERS:
        raise ValueError(
            f"exact store Shapley requires n <= {MAX_EXACT_PLAYERS}; got n={n_stores}."
        )
    v_by_mask = np.empty(1 << n_stores, dtype=np.float64)
    for s in range(1 << n_stores):
        subset = {i for i in range(n_stores) if (s >> i) & 1}
        v_by_mask[s] = _store_value(tau_hat, home_store, item_store, subset)
    phi = shapley_exact_by_mask(n_stores, v_by_mask)
    v_n = float(v_by_mask[-1])
    return {
        "phi": phi,
        "v_n": v_n,
        "efficiency_max_residual": abs(float(phi.sum()) - v_n),
    }


def store_shapley_monte_carlo(
    tau_hat: np.ndarray,
    home_store: np.ndarray,
    item_store: np.ndarray,
    n_stores: int,
    n_permutations: int = 100,
    seed: int = 20240914,
) -> dict[str, np.ndarray | float]:
    """Monte Carlo store-level Shapley (same value function, sampled orders)."""
    tau_hat = np.asarray(tau_hat, dtype=np.float64)
    home_store = np.asarray(home_store, dtype=np.int64)
    item_store = np.asarray(item_store, dtype=np.int64)
    v_fn = lambda S: _store_value(tau_hat, home_store, item_store, S)  # noqa: E731
    phi, var = shapley_monte_carlo(n_stores, v_fn, n_permutations, seed)
    v_n = _store_value(tau_hat, home_store, item_store, set(range(n_stores)))
    return {
        "phi": phi,
        "sampling_variance": var,
        "v_n": float(v_n),
        "efficiency_max_residual": abs(float(phi.sum()) - float(v_n)),
    }

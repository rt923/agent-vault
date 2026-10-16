"""T-learner uplift modeling for the v0.2 `uplift_attribution` task kind.

[想象含量 L1][证据锚: §4.2-表4.2 T-learner 最优 PEHE=1.9485]

T-learner (双模型法, §4.2.2): fit two separate regressors
  mu1 on the treated arm (T=1), mu0 on the control arm (T=0);
  tau_hat(x) = mu1(x) - mu0(x).

BOUNDARY (equivalent implementation, §4.3.2):
  The paper's exact regressor family / hyperparameters are unavailable on
  this machine (oulai-pipeline-integrated.py absent). We use sklearn
  GradientBoostingRegressor for both arms -- a deterministic nonlinear
  regressor. PEHE on our regenerated synthetic dataset is expected to land
  in the same order of magnitude as 表4.2 (T-learner 1.9485, S-learner
  3.8599) but exact equality is NOT guaranteed.

Metrics use the SAME formulas as judge v0.2 (指标同尺):
  - pehe     = sqrt(mean((tau_hat - tau_true)^2))   [L1][§4.2 PEHE定义]
  - ate_bias = |mean(tau_hat) - mean(tau_true)|     [L1][§4.2 ATE偏差列]
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

try:
    from .synthetic_uplift import UpliftDataset
except ImportError:  # flat run (python main.py)
    from synthetic_uplift import UpliftDataset


@dataclass
class TLearner:
    """Trained T-learner artifact (joblib-picklable)."""

    mu0: GradientBoostingRegressor
    mu1: GradientBoostingRegressor
    feature_names: list[str]
    store_names: list[str]

    def predict_tau(self, X: np.ndarray) -> np.ndarray:
        """tau_hat(x) = mu1(x) - mu0(x) for a batch (n, d)."""
        X = np.asarray(X, dtype=np.float64)
        return self.mu1.predict(X) - self.mu0.predict(X)


def train_uplift(ds: UpliftDataset, seed: int = 20240914) -> TLearner:
    """Fit the two arms on the training split (RCT: T independent of X)."""
    X, T, Y = ds.X, ds.T, ds.Y
    t_mask = T == 1
    mu1 = GradientBoostingRegressor(random_state=seed)
    mu0 = GradientBoostingRegressor(random_state=seed)
    mu1.fit(X[t_mask], Y[t_mask])
    mu0.fit(X[~t_mask], Y[~t_mask])
    return TLearner(
        mu0=mu0,
        mu1=mu1,
        feature_names=list(ds.feature_names),
        store_names=list(ds.store_names),
    )


def uplift_metrics(tau_hat: np.ndarray, tau_true: np.ndarray) -> dict[str, float]:
    """Judge-aligned regression metrics on the eval split (指标同尺)."""
    tau_hat = np.asarray(tau_hat, dtype=np.float64)
    tau_true = np.asarray(tau_true, dtype=np.float64)
    return {
        "pehe": float(np.sqrt(((tau_hat - tau_true) ** 2).mean())),
        "ate_bias": float(abs(tau_hat.mean() - tau_true.mean())),
    }

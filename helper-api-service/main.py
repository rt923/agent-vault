"""helper-api service -- FastAPI app.

Routes (aligned to coder-app.dsl.yaml provider_id=helper-api, tool_name
train / infer / baselines):
  GET  /health      liveness
  POST /train       train by config.task_kind:
                      intent_clf          -> TF-IDF+LogReg (BANKING77 proxy, v0.1)
                      uplift_attribution  -> T-learner tau_hat + PEHE/ate_bias (v0.2)
  POST /infer       run inference (classification: {query_id, prediction, confidence};
                    uplift: {query_id, prediction: tau_hat float})
  POST /shapley     v0.2 dual-level Shapley on an uplift run
                    (level: feature|store; algorithm: exact|monte_carlo, caller-
                    explicit; exact hard-caps n<=20 per paper L303)
  POST /baselines   report 3 baselines' macro_f1 + accuracy on the eval split
                    (intent_clf only; uplift baseline = 表4.2 model comparison, step 3)

Import pattern: supports both `python main.py` (flat) and package import.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime

import numpy as np
from fastapi import FastAPI, HTTPException

try:
    from .config import settings
    from .data_adapter import get_dataset
    from .schemas import (
        BaselinesRequest,
        BaselinesResponse,
        BaselineEntry,
        InferRequest,
        InferResponse,
        InferOutputItem,
        ShapleyRequest,
        ShapleyResponse,
        TrainRequest,
        TrainResponse,
    )
    from .store import ModelStore
    from .train import train_intent_clf
    from .infer import infer as run_infer
    from .baselines import compute_baselines, SUPPORTED_METRICS
    from .metrics import compute_metrics
    from .uplift import train_uplift, uplift_metrics
    from .shapley_attr import (
        feature_shapley_exact,
        feature_shapley_monte_carlo,
        store_shapley_exact,
        store_shapley_monte_carlo,
    )
    from .synthetic_uplift import N_ITEMS, N_USERS, TRUE_PHI, generate, split_train_eval
except ImportError:  # flat run (python main.py)
    from config import settings
    from data_adapter import get_dataset
    from schemas import (
        BaselinesRequest,
        BaselinesResponse,
        BaselineEntry,
        InferRequest,
        InferResponse,
        InferOutputItem,
        ShapleyRequest,
        ShapleyResponse,
        TrainRequest,
        TrainResponse,
    )
    from store import ModelStore
    from train import train_intent_clf
    from infer import infer as run_infer
    from baselines import compute_baselines, SUPPORTED_METRICS
    from metrics import compute_metrics
    from uplift import train_uplift, uplift_metrics
    from shapley_attr import (
        feature_shapley_exact,
        feature_shapley_monte_carlo,
        store_shapley_exact,
        store_shapley_monte_carlo,
    )
    from synthetic_uplift import N_ITEMS, N_USERS, TRUE_PHI, generate, split_train_eval


app = FastAPI(title="helper-api", version="0.2.0")

# Module-level singletons.
_store = ModelStore(settings.models_dir)
_latest_run_id: str | None = _store.latest_run_id()


def _split_indices(n: int, frac: float, seed: int) -> tuple[list[int], list[int]]:
    """Deterministic train/eval split (eval = first n*frac after shuffle)."""
    idx = list(range(n))
    rng = random.Random(seed)
    rng.shuffle(idx)
    n_eval = max(1, int(n * frac))
    return idx[n_eval:], idx[:n_eval]  # train, eval


def _make_run_id(task_kind: str) -> str:
    base = f"{task_kind}-{datetime.now():%Y%m%d%H%M%S}"
    if _store.exists(base):
        base = f"{base}-{uuid.uuid4().hex[:4]}"
    return base


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/train", response_model=TrainResponse)
def train(req: TrainRequest) -> TrainResponse:
    global _latest_run_id  # single declaration (CPython: duplicate `global`
    # after an intervening assignment is a SyntaxError)
    task_kind = (req.config or {}).get("task_kind", settings.default_task_kind)
    if task_kind not in settings.supported_task_kinds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported task_kind '{task_kind}'; supported: "
                f"{settings.supported_task_kinds}"
            ),
        )
    if req.mode != "full":
        raise HTTPException(
            status_code=400,
            detail="mode 'incremental' not implemented (use 'full')",
        )

    if task_kind == "uplift_attribution":
        # v0.2 mainline: synthetic data (step 0 generator) -> T-learner -> PEHE/ate_bias.
        # [L1][证据锚: §4.11 配置; §4.2-表4.2 T-learner 最优]
        seed = int((req.config or {}).get("seed", settings.uplift_seed))
        n_users = int((req.config or {}).get("n_users", N_USERS))
        n_items = int((req.config or {}).get("n_items", N_ITEMS))
        ds = generate(seed=seed, n_users=n_users, n_items=n_items)
        train_ds, eval_ds = split_train_eval(
            ds, eval_fraction=settings.eval_fraction, seed=seed
        )
        model = train_uplift(train_ds, seed=seed)
        tau_hat = model.predict_tau(eval_ds.X)
        metrics = uplift_metrics(tau_hat, eval_ds.tau_true)

        run_id = _make_run_id(task_kind)
        _store.save(
            run_id,
            model,
            metrics,
            {
                "task_kind": task_kind,
                "n_train": len(train_ds.T),
                "n_eval": len(eval_ds.T),
                "seed": seed,
                "n_users": n_users,
                "n_items": n_items,
                "eval_fraction": settings.eval_fraction,
                "feature_names": ds.feature_names,
                "store_names": ds.store_names,
            },
        )
        _latest_run_id = run_id
        return TrainResponse(
            run_id=run_id,
            status="completed",
            task_kind=task_kind,
            n_train=len(train_ds.T),
            metrics=metrics,
        )

    # v0.1 path: intent_clf (BANKING77 proxy)
    ds = get_dataset(
        settings.data_source,
        settings.local_dataset_path,
        req.dataset_id or settings.dify_dataset_id,
        settings.dify_api_base,
        settings.dify_api_key,
    )
    n = len(ds.texts)
    if n == 0:
        raise HTTPException(status_code=400, detail="empty dataset")

    train_idx, eval_idx = _split_indices(n, settings.eval_fraction, settings.eval_seed)
    model = train_intent_clf(
        [ds.texts[i] for i in train_idx], [ds.labels[i] for i in train_idx]
    )

    ev_texts = [ds.texts[i] for i in eval_idx]
    ev_labels = [ds.labels[i] for i in eval_idx]
    ev_preds, _ = run_infer(model, ev_texts)
    metrics = compute_metrics(ev_labels, ev_preds)

    run_id = _make_run_id(task_kind)
    _store.save(
        run_id,
        model,
        metrics,
        {
            "task_kind": task_kind,
            "n_train": len(train_idx),
            "n_classes": len(ds.label_names),
            "label_names": ds.label_names,
        },
    )
    _latest_run_id = run_id
    return TrainResponse(
        run_id=run_id,
        status="completed",
        task_kind=task_kind,
        n_train=len(train_idx),
        metrics=metrics,
    )


@app.post("/infer", response_model=InferResponse)
def infer_endpoint(req: InferRequest) -> InferResponse:
    global _latest_run_id
    run_id = req.run_id or _latest_run_id
    if not run_id:
        raise HTTPException(
            status_code=400,
            detail="no run_id provided and no model trained yet; call /train first",
        )
    if not _store.exists(run_id):
        raise HTTPException(status_code=404, detail=f"run_id not found: {run_id}")

    model, meta = _store.load(run_id)

    if meta.get("task_kind") == "uplift_attribution":
        # v0.2: features -> tau_hat (float, judge /submit same-scale)
        feats = [it.features for it in req.inputs]
        if any(f is None for f in feats):
            raise HTTPException(
                status_code=400,
                detail="uplift_attribution infer requires inputs[].features (float vector)",
            )
        X = np.asarray(feats, dtype=np.float64)
        taus = model.predict_tau(X)
        outputs = [
            InferOutputItem(query_id=it.query_id, prediction=float(t), confidence=None)
            for it, t in zip(req.inputs, taus)
        ]
        return InferResponse(outputs=outputs, run_id=run_id)

    # v0.1 path: classification (text -> integer class id)
    texts = [it.text for it in req.inputs]
    if any(t is None for t in texts):
        raise HTTPException(
            status_code=400,
            detail="classification infer requires inputs[].text",
        )
    preds, confs = run_infer(model, texts)
    outputs = [
        InferOutputItem(query_id=it.query_id, prediction=p, confidence=c)
        for it, p, c in zip(req.inputs, preds, confs)
    ]
    return InferResponse(outputs=outputs, run_id=run_id)


@app.post("/shapley", response_model=ShapleyResponse)
def shapley_endpoint(req: ShapleyRequest) -> ShapleyResponse:
    """v0.2 dual-level Shapley on an uplift_attribution run.

    feature: 表4.3 semantics (10 features, per-sample Shapley of tau_hat,
             baseline zeros, efficiency hard-checked).
    store:   表4.10 semantics (alliance-formation value function on
             home_store x item_store flows; NOT the lambda* equilibrium).
    """
    global _latest_run_id
    run_id = req.run_id or _latest_run_id
    if not run_id:
        raise HTTPException(
            status_code=400,
            detail="no run_id provided and no model trained yet; call /train first",
        )
    if not _store.exists(run_id):
        raise HTTPException(status_code=404, detail=f"run_id not found: {run_id}")

    model, meta = _store.load(run_id)
    if meta.get("task_kind") != "uplift_attribution":
        raise HTTPException(
            status_code=400,
            detail=f"run {run_id} is task_kind={meta.get('task_kind')!r}; "
            "/shapley requires an uplift_attribution run",
        )

    seed = req.seed if req.seed is not None else int(meta.get("seed", settings.uplift_seed))
    ds = generate(
        seed=seed,
        n_users=int(meta.get("n_users", N_USERS)),
        n_items=int(meta.get("n_items", N_ITEMS)),
    )
    _train_ds, eval_ds = split_train_eval(
        ds, eval_fraction=float(meta.get("eval_fraction", settings.eval_fraction)), seed=seed
    )
    tau_hat = model.predict_tau(eval_ds.X)

    try:
        if req.level == "feature":
            names = meta.get("feature_names") or ds.feature_names
            # 门店等级 ≡ 6−home_store (deterministic store proxy): held at its
            # OBSERVED value in every masked variant (conditional attribution),
            # so its φ is exactly 0 and the store intercept cannot leak into
            # it. Store-tier attribution lives in 表4.4 / 表4.10 (store level).
            # Design interpretation under §5 -- the paper's exact value
            # function is not recoverable from the disclosed text.
            FIXED_FEATURE = "门店等级"
            fixed_features = (
                [names.index(FIXED_FEATURE)] if FIXED_FEATURE in names else None
            )
            if req.algorithm == "exact":
                res = feature_shapley_exact(
                    model.predict_tau, eval_ds.X, fixed_features=fixed_features
                )
            else:
                res = feature_shapley_monte_carlo(
                    model.predict_tau, eval_ds.X,
                    n_permutations=req.n_permutations, seed=seed,
                    fixed_features=fixed_features,
                )
            phi_arr = np.asarray(res["phi_means"])
            phi_means = {names[i]: float(phi_arr[i]) for i in range(len(names))}

            # truth side, same value function + zero baseline: phi_k * E[x_k]
            true_same_scale = TRUE_PHI * eval_ds.X.mean(axis=0)
            if fixed_features is not None:
                true_same_scale[fixed_features[0]] = 0.0  # fixed => φ exactly 0
            errors = phi_arr - true_same_scale
            var_arr = res.get("sampling_variance")
            return ShapleyResponse(
                run_id=run_id,
                level=req.level,
                algorithm=req.algorithm,
                n_players=len(names),
                phi_means=phi_means,
                efficiency_max_residual=float(res["efficiency_max_residual"]),
                sampling_variance=(
                    {names[i]: float(np.asarray(var_arr)[i]) for i in range(len(names))}
                    if var_arr is not None
                    else None
                ),
                details={
                    "n_eval": len(eval_ds.T),
                    "baseline": "zeros",
                    "fixed_features": (
                        {
                            "names": [names[i] for i in fixed_features],
                            "rationale": (
                                "门店等级≡6−home_store is a deterministic store "
                                "proxy; held at observed value (conditional "
                                "attribution, φ≡0) so segment levels belong to "
                                "表4.4/表4.10 store-level decomposition. Design "
                                "interpretation (§5): the paper's exact value "
                                "function is not recoverable from the disclosed text."
                            ),
                        }
                        if fixed_features
                        else None
                    ),
                    "true_phi_structural": {  # paper 表4.3 scale (generator coefficients)
                        ds.feature_names[i]: float(TRUE_PHI[i])
                        for i in range(len(ds.feature_names))
                    },
                    "true_phi_means_same_scale": {  # phi_k * E[x_k], same ruler
                        ds.feature_names[i]: float(true_same_scale[i])
                        for i in range(len(ds.feature_names))
                    },
                    "attribution_errors_same_scale": {
                        ds.feature_names[i]: float(errors[i])
                        for i in range(len(ds.feature_names))
                    },
                    "mean_abs_error_same_scale": float(np.abs(errors).mean()),
                },
            )

        # store level
        n_stores = len(ds.store_names)
        if req.algorithm == "exact":
            res = store_shapley_exact(tau_hat, eval_ds.home_store, eval_ds.item_store, n_stores)
        else:
            res = store_shapley_monte_carlo(
                tau_hat, eval_ds.home_store, eval_ds.item_store, n_stores,
                n_permutations=req.n_permutations, seed=seed,
            )
        phi_arr = np.asarray(res["phi"])
        var_arr = res.get("sampling_variance")
        return ShapleyResponse(
            run_id=run_id,
            level=req.level,
            algorithm=req.algorithm,
            n_players=n_stores,
            phi_means={ds.store_names[i]: float(phi_arr[i]) for i in range(n_stores)},
            efficiency_max_residual=float(res["efficiency_max_residual"]),
            sampling_variance=(
                {ds.store_names[i]: float(np.asarray(var_arr)[i]) for i in range(n_stores)}
                if var_arr is not None
                else None
            ),
            details={
                "n_eval": len(eval_ds.T),
                "v_n_total": float(res["v_n"]),
                "value_function": "v(S)=sum(tau_hat | home_store in S AND item_store in S)",
                "boundary_note": (
                    "alliance-formation attribution on synthetic flows; paper 表4.10 "
                    "is the lambda*=1.00 degenerate solution and is NOT evidence of "
                    "the transfer-payment mechanism working standalone"
                ),
            },
        )
    except ValueError as exc:
        # exact-mode n>20 cap and shape errors -> 400 (caller must opt into MC)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/baselines", response_model=BaselinesResponse)
def baselines_endpoint(req: BaselinesRequest) -> BaselinesResponse:
    ds = get_dataset(
        settings.data_source,
        settings.local_dataset_path,
        req.task_id or settings.dify_dataset_id,
        settings.dify_api_base,
        settings.dify_api_key,
    )
    n = len(ds.texts)
    if n == 0:
        raise HTTPException(status_code=400, detail="empty dataset")

    _train_idx, eval_idx = _split_indices(n, settings.eval_fraction, settings.eval_seed)
    ev_texts = [ds.texts[i] for i in eval_idx]
    ev_labels = [ds.labels[i] for i in eval_idx]

    wanted = [m for m in (req.metrics or SUPPORTED_METRICS) if m in SUPPORTED_METRICS]
    if not wanted:
        wanted = list(SUPPORTED_METRICS)
    entries = compute_baselines(
        ev_texts, ev_labels, seed=settings.eval_seed, metrics=tuple(wanted)
    )
    return BaselinesResponse(
        baselines=entries,
        n_examples=len(ev_labels),
        task_kind=settings.default_task_kind,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)

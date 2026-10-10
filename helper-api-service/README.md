# helper-api service (v0.1)

FastAPI service backing the `helper-api` Dify tool provider referenced by
`coder-app.dsl.yaml` (provider_id `helper-api`, tool_name `train` / `infer` /
`baselines`). This is issue **#52**'s engineering-track implementation, turning
the contract-only `dify-dsl/helper-api.openapi.yaml` into a working service.

> **Proxy task, not the business mainline.** v0.1 implements exactly one
> declarative `task_kind`: `intent_clf` (BANKING77 77-way intent classification).
> It exists to wire the full `/train → /infer → /baselines` loop end-to-end.
> The Shapley cross-store attribution mainline (paper Ch.4) is **v0.2** and only
> lands once the business data assets exist — we do **not** fabricate data.

## Endpoints

| Method | Path        | Purpose                                                        |
| ------ | ----------- | ------------------------------------------------------------- |
| GET    | `/health`   | liveness                                                       |
| POST   | `/train`    | train `intent_clf`, save artifact, return `run_id` + metrics  |
| POST   | `/infer`    | infer; `outputs=[{query_id, prediction:int, confidence:float}]`|
| POST   | `/baselines`| 3 baselines × {macro_f1, accuracy} + top-level `n_examples`    |

### Contract pins (from `_design/helper-api-oq-decision-card.md`)
- **OQ-1** `config.task_kind` declarative; `mode="full"` sync-blocking; artifacts
  in `{models_dir}/{run_id}/` (`model.joblib` + `metrics.json`).
- **OQ-2** `inputs=[{query_id, text}]`; `prediction` is an **int** class id
  (judge same-scale), `confidence` = max softmax prob.
- **OQ-3** baselines = `majority-class` / `uniform-random` / `tfidf-logreg`;
  metrics = **macro_f1 (primary) + accuracy (secondary)** only (judge's sole
  external scale); response = `baselines[{name, metric, value}]` + `n_examples`.
- **OQ-4** deploy as k3d Deployment + ClusterIP (ns `dify`), container 8799,
  NodePort 30799; base url `http://helper-api.dify.svc.cluster.local:8799`.

## Data adapter (OQ-1.2)
- `HELPER_DATA_SOURCE=dify` → Dify Dataset API (`DIFY_DATASET_ID` +
  `DIFY_API_KEY`); the production single source of truth.
- `HELPER_DATA_SOURCE=local` (default) → bundled offline BANKING77 at
  `data/banking77_train.json` (dev / smoke / when no dataset is wired).

## Run locally

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
# HELPER_MODELS_DIR defaults to /models; override on Windows:
set HELPER_MODELS_DIR=%TEMP%\helper-models
python main.py            # serves :8799
# or: uvicorn main:app --port 8799
```

## Smoke test

```bash
python smoke_test.py     # 5 contract assertions, no live server needed
```

## Deploy (see `k8s.yaml` + `Dockerfile`, task #56)

```bash
docker build -t helper-api:0.1.0 .
# load into k3d-fresh-env, then:
kubectl --context k3d-fresh-env -n dify apply -f k8s.yaml
```

"""Data adapter for helper-api training/eval.

Production adapter (OQ-1.2): Dify Dataset API -- the dataset is the single
source of truth. Dev fallback: bundled offline BANKING77 (crossfit format),
used for smoke runs and when no Dify dataset is wired. We never fabricate
business data (the Shapley mainline stays v0.2 until assets land).
"""
from __future__ import annotations

import json
import os
from typing import NamedTuple


class Dataset(NamedTuple):
    texts: list[str]
    labels: list[int]  # integer class ids (judge same-scale contract)
    label_names: list[str]  # index -> human-readable class name


def _parse_crossfit(raw: dict) -> Dataset:
    """Parse the PolyAI/Banking77 'crossfit' dict format found in the bundle.

    Each record: {"turns":[{"role":"user","text":...,"extra_info":{"intent_label":N},
                            "label":{"DEFAULT_DOMAIN":{"<name>":{}}}}]}.
    Falls back gracefully if intent_label is absent (map by name).
    """
    texts: list[str] = []
    labels: list[int] = []
    name_by_idx: dict[int, str] = {}
    name_to_idx: dict[str, int] = {}

    for rec in raw.values():
        if not isinstance(rec, dict):
            continue
        turns = rec.get("turns") or []
        user_texts = [t.get("text", "") for t in turns if isinstance(t, dict) and t.get("role") == "user"]
        if not user_texts:
            txt = rec.get("text") or rec.get("utterance") or ""
            user_texts = [txt] if txt else []
        text = " ".join(user_texts).strip()
        if not text:
            continue

        y: int | None = None
        name: str | None = None
        for t in turns:
            if not isinstance(t, dict):
                continue
            ei = t.get("extra_info") or {}
            if isinstance(ei, dict) and "intent_label" in ei:
                try:
                    y = int(ei["intent_label"])
                except (TypeError, ValueError):
                    y = None
            dom = (t.get("label") or {}).get("DEFAULT_DOMAIN") or {}
            if isinstance(dom, dict) and dom:
                name = next(iter(dom.keys()))
        if y is None:
            ei = rec.get("extra_info") or {}
            if isinstance(ei, dict) and "intent_label" in ei:
                try:
                    y = int(ei["intent_label"])
                except (TypeError, ValueError):
                    y = None
        if y is None and name is not None:
            if name not in name_to_idx:
                name_to_idx[name] = len(name_to_idx)
            y = name_to_idx[name]
        if y is None:
            continue

        texts.append(text)
        labels.append(y)
        if name is not None:
            name_by_idx[y] = name

    max_idx = max(labels) if labels else -1
    label_names = [name_by_idx.get(i, f"cls_{i}") for i in range(max_idx + 1)]
    return Dataset(texts, labels, label_names)


def load_local(path: str) -> Dataset:
    """Load the bundled/offline BANKING77 file."""
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"local dataset not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        # alternate format: list of {"text", "label"}
        texts, labels, names = [], [], {}
        for i, e in enumerate(raw):
            texts.append(e.get("text", ""))
            lab = e.get("label")
            if isinstance(lab, int):
                y = lab
            else:
                if lab not in names:
                    names[lab] = len(names)
                y = names[lab]
            labels.append(y)
            if isinstance(lab, str):
                names_idx = {v: k for k, v in names.items()}
                if y in names_idx:
                    pass
        # rebuild label_names
        name_by_idx = {}
        nm = {}
        for e in raw:
            lab = e.get("label")
            if isinstance(lab, str) and lab not in nm:
                nm[lab] = len(nm)
        label_names = [nm.get(i) or f"cls_{i}" for i in range(len(nm))]
        # recompute labels via nm
        labels = [(nm[e["label"]] if isinstance(e.get("label"), str) else e["label"]) for e in raw]
        return Dataset(texts, labels, label_names)
    return _parse_crossfit(raw)


def load_dify(dataset_id: str, api_base: str, api_key: str) -> Dataset:
    """Production adapter: pull a Dify dataset's documents + segments.

    Assumed schema per document segment:
      - content: the raw text (training example)
      - metadata.label OR keywords[0]: the integer/string class
    Requires the `requests` package (only needed for the dify source).
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Dify data source requires `requests`; install it or use "
            "HELPER_DATA_SOURCE=local"
        ) from exc

    headers = {"Authorization": f"Bearer {api_key}"}
    texts, labels = [], []
    nm: dict[str, int] = {}

    page = 1
    while True:
        r = requests.get(
            f"{api_base}/v1/datasets/{dataset_id}/documents",
            headers=headers,
            params={"page": page, "limit": 100},
            timeout=30,
        )
        r.raise_for_status()
        docs = r.json().get("data", [])
        if not docs:
            break
        for doc in docs:
            doc_id = doc["id"]
            sr = requests.get(
                f"{api_base}/v1/datasets/{dataset_id}/documents/{doc_id}/segments",
                headers=headers,
                timeout=30,
            )
            sr.raise_for_status()
            for seg in sr.json().get("data", []):
                content = seg.get("content") or ""
                meta = seg.get("metadata") or {}
                label = meta.get("label")
                if label is None and seg.get("keywords"):
                    label = seg["keywords"][0]
                if not content or label is None:
                    continue
                if isinstance(label, int):
                    y = label
                else:
                    if label not in nm:
                        nm[label] = len(nm)
                    y = nm[label]
                texts.append(content)
                labels.append(y)
        if len(docs) < 100:
            break
        page += 1

    label_names = [nm.get(i, f"cls_{i}") for i in range(len(nm))]
    return Dataset(texts, labels, label_names)


def get_dataset(
    source: str,
    local_path: str,
    dataset_id: str | None = None,
    dify_api_base: str = "",
    dify_api_key: str = "",
) -> Dataset:
    """Dispatch by source. Local is the offline fallback (OQ-1.2 dev path)."""
    if source == "dify":
        if not dataset_id or not dify_api_key:
            raise ValueError(
                "Dify source requires DIFY_DATASET_ID and DIFY_API_KEY env; "
                "fall back to HELPER_DATA_SOURCE=local."
            )
        return load_dify(dataset_id, dify_api_base, dify_api_key)
    return load_local(local_path)

from __future__ import annotations

from pathlib import Path

import joblib
from django.conf import settings

from ml.models import ModelVersion


def artifact_dir(name: str) -> Path:
    p = Path(settings.ARTIFACTS_DIR) / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def next_version(name: str) -> int:
    last = ModelVersion.objects.filter(name=name).order_by("-version").first()
    return 1 if last is None else last.version + 1


def _jsonable(metrics: dict) -> dict:
    out = {}
    for k, v in metrics.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            try:
                # numpy scalars
                out[k] = v.item() if hasattr(v, "item") else v
            except Exception:
                out[k] = v
    return out


def save(model_bundle: dict, metrics: dict, name: str = "swing_lgbm", status: str = "candidate") -> ModelVersion:
    ver = next_version(name)
    path = artifact_dir(name) / f"v{ver}.joblib"
    # Don't pickle calibrator/model failures: dump whole bundle
    joblib.dump(model_bundle, path)
    train_start = metrics.get("train_start")
    train_end = metrics.get("train_end")
    mv = ModelVersion.objects.create(
        name=name,
        version=ver,
        algo=model_bundle.get("algo", "lightgbm"),
        params=model_bundle.get("params", {}),
        train_start=train_start,
        train_end=train_end,
        metrics=_jsonable(metrics),
        artifact_path=str(path),
        status=status,
    )
    return mv


def load(mv: ModelVersion) -> dict:
    return joblib.load(mv.artifact_path)


def champion(name: str = "swing_lgbm") -> ModelVersion | None:
    return ModelVersion.objects.filter(name=name, status=ModelVersion.Status.CHAMPION).order_by("-version").first()


def promote(mv: ModelVersion) -> ModelVersion:
    ModelVersion.objects.filter(name=mv.name, status=ModelVersion.Status.CHAMPION).update(
        status=ModelVersion.Status.RETIRED
    )
    mv.status = ModelVersion.Status.CHAMPION
    mv.save(update_fields=["status"])
    return mv


def rollback(name: str = "swing_lgbm") -> ModelVersion | None:
    current = champion(name)
    prev = (
        ModelVersion.objects.filter(name=name, status=ModelVersion.Status.RETIRED)
        .order_by("-version")
        .first()
    )
    if prev is None:
        return current
    if current:
        current.status = ModelVersion.Status.RETIRED
        current.save(update_fields=["status"])
    prev.status = ModelVersion.Status.CHAMPION
    prev.save(update_fields=["status"])
    return prev

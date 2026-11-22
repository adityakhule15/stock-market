import pytest

from ml import registry
from ml.models import ModelVersion


@pytest.mark.django_db
def test_promote_and_rollback(tmp_path, settings):
    settings.ARTIFACTS_DIR = tmp_path
    bundle = {"algo": "lightgbm", "model": None, "params": {}, "feature_cols": []}
    # save needs joblib.dump of bundle — model None is fine for this test
    mv1 = registry.save(bundle, metrics={"expectancy_R": 0.1, "max_dd": 1, "brier": 0.2, "n_signals": 40}, name="t")
    registry.promote(mv1)
    assert registry.champion("t").id == mv1.id
    mv2 = registry.save(bundle, metrics={"expectancy_R": 0.2, "max_dd": 1, "brier": 0.1, "n_signals": 40}, name="t")
    registry.promote(mv2)
    assert registry.champion("t").id == mv2.id
    mv1.refresh_from_db()
    assert mv1.status == ModelVersion.Status.RETIRED
    rolled = registry.rollback("t")
    assert rolled.id == mv1.id

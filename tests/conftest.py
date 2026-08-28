
import pytest

from anchor import Anchor, AnchorConfig


@pytest.fixture()
def cfg(tmp_path):
    return AnchorConfig(
        db_path=str(tmp_path / "test.db"),
        min_sources_for_active=1,
        promote_threshold=0.66,
    )


@pytest.fixture()
def anchor(cfg):
    a = Anchor(cfg)
    yield a
    a.close()

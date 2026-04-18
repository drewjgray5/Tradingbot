from __future__ import annotations

import os

from env_overrides import temporary_env as _temporary_env


def test_temporary_env_sets_and_restores_existing_keys() -> None:
    os.environ["TB_ENV_TEST_EXISTING"] = "before"
    with _temporary_env({"TB_ENV_TEST_EXISTING": "during"}):
        assert os.environ.get("TB_ENV_TEST_EXISTING") == "during"
    assert os.environ.get("TB_ENV_TEST_EXISTING") == "before"
    os.environ.pop("TB_ENV_TEST_EXISTING", None)


def test_temporary_env_removes_keys_missing_before_context() -> None:
    os.environ.pop("TB_ENV_TEST_NEW", None)
    with _temporary_env({"TB_ENV_TEST_NEW": "during"}):
        assert os.environ.get("TB_ENV_TEST_NEW") == "during"
    assert "TB_ENV_TEST_NEW" not in os.environ


def test_temporary_env_restores_after_exception() -> None:
    os.environ["TB_ENV_TEST_EXCEPTION"] = "before"
    try:
        with _temporary_env({"TB_ENV_TEST_EXCEPTION": "during"}):
            assert os.environ.get("TB_ENV_TEST_EXCEPTION") == "during"
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert os.environ.get("TB_ENV_TEST_EXCEPTION") == "before"
    os.environ.pop("TB_ENV_TEST_EXCEPTION", None)

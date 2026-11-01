"""Test setup: a throwaway SQLite database, AI and online voice switched
off (tests replace the AI with fakes where needed). Your .env is not
changed; these environment values simply take priority."""
import os
import pathlib
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="lexiguard-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["GROQ_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["LLM_API_URL"] = ""
os.environ["USE_LLM"] = "false"
os.environ["TTS_ENGINE"] = "browser"
os.environ["TTS_CACHE_DIR"] = f"{_tmp}/audio"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from db import init_db  # noqa: E402

init_db()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    import auth
    for lim in (auth.login_limiter, auth.signup_limiter):
        lim._hits.clear()
    yield

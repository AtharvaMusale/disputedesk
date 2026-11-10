from disputedesk.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.llm_provider == "gemini"
    assert s.duckdb_path.endswith(".duckdb")
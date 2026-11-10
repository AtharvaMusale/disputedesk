from disputedesk.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.llm_provider == "gemini"
    assert s.duckdb_path.endswith(".duckdb")

def test_policy_limit_matches_config_default():
    from disputedesk.synthetic import policies

    assert policies.MKT_LIMIT == Settings(_env_file=None).refund_approval_limit_usd

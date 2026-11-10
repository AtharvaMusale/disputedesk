"""The CFPB outcome labels are the ground truth for real cases; a swapped mapping would flip all of them."""
from pathlib import Path

import duckdb
import pytest

from disputedesk.config import settings
from disputedesk.data.download_cfpb import LABELS


def test_label_mapping_constants():
    assert LABELS == {"Closed with monetary relief": "approve",
                      "Closed with non-monetary relief": "partial",
                      "Closed with explanation": "deny"}


@pytest.mark.skipif(not Path(settings.duckdb_path).exists(), reason="run make load")
def test_labels_in_loaded_data_follow_company_response():
    con = duckdb.connect(settings.duckdb_path, read_only=True)
    wrong = con.execute("""SELECT count(*) FROM real.cfpb_complaints WHERE label IS DISTINCT FROM CASE company_response
        WHEN 'Closed with monetary relief' THEN 'approve' WHEN 'Closed with non-monetary relief' THEN 'partial'
        WHEN 'Closed with explanation' THEN 'deny' END""").fetchone()[0]
    groups = con.execute("""SELECT count(*) FROM real.cfpb_complaints
        WHERE (product_group = 'money_transfer') <> (product ILIKE 'Money transfer%' OR product = 'Virtual currency')""").fetchone()[0]
    assert wrong == 0 and groups == 0

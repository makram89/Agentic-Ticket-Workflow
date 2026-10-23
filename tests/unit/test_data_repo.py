"""DataRepo: KB search (S-37) and seed loader (S-59)."""
import json
import shutil
from pathlib import Path

import pytest

from tests import fixtures as fx

ROOT = Path(__file__).resolve().parents[2]


def test_s37_search_kb_token_overlap_ranking(repo):
    """S-37"""
    from ticketflow.models import KbArticle

    hits = repo.search_kb("I forgot my password")
    assert 1 <= len(hits) <= 3
    assert all(isinstance(h, KbArticle) for h in hits)
    assert hits[0].article_id == "KB-PW"
    assert repo.search_kb("pizza place") == []
    assert repo.search_kb("") == []


def test_s37_search_kb_limit_and_tie_order(repo):
    """S-37: at most `limit` results; ties broken by article_id."""
    # "invoice" hits KB-SUSP ("invoice") only; "invoices" hits KB-BILL only -> equal score 1, sorted by id
    hits = repo.search_kb("invoices invoice")
    assert [h.article_id for h in hits] == ["KB-BILL", "KB-SUSP"]
    assert len(repo.search_kb("password outage suspended invoices", limit=2)) == 2
    # stopwords and short tokens do not match anything
    assert repo.search_kb("the and for you your are can with this") == []


def test_s59_seed_loader_loads_valid_collections():
    """S-59 (no content assertions on seed data)"""
    from ticketflow.data.repository import DataRepo
    from ticketflow.models import Customer, Ticket

    repo = DataRepo.from_seed()
    tickets = repo.tickets()
    assert tickets and all(isinstance(t, Ticket) for t in tickets)
    assert repo.known_article_ids()
    assert any(isinstance(repo.get_customer(t.customer_id), Customer) for t in tickets)


def _drop_first_amount(node):
    if isinstance(node, dict):
        if "amount_eur" in node:
            del node["amount_eur"]
            return True
        return any(_drop_first_amount(v) for v in node.values())
    if isinstance(node, list):
        return any(_drop_first_amount(v) for v in node)
    return False


def test_s59_malformed_seed_file_error_names_file(tmp_path):
    """S-59"""
    from ticketflow.data.repository import DataRepo

    seed = ROOT / "ticketflow" / "data" / "seed"
    target = tmp_path / "seed"
    shutil.copytree(seed, target)
    inv_path = target / "invoices.json"
    data = json.loads(inv_path.read_text())
    assert _drop_first_amount(data), "invoices.json has no amount_eur entry"
    inv_path.write_text(json.dumps(data))

    with pytest.raises(Exception) as excinfo:
        DataRepo.from_seed(target)
    assert "invoices.json" in str(excinfo.value)


def test_s59_repo_from_in_memory_lists(repo):
    """S-59"""
    cust = repo.get_customer("CUST-A")
    assert cust is not None and cust.plan == "business"
    assert repo.get_customer("CUST-X") is None
    assert sorted(i.invoice_id for i in repo.get_invoices("CUST-A")) == ["INV-A1", "INV-A2"]
    assert repo.get_invoices("CUST-X") == []
    assert repo.search_kb("outage status")[0].article_id == "KB-STATUS"
    assert sorted(repo.known_article_ids()) == sorted(fx.KB_IDS)
    assert repo.tickets() == []

"""DataRepo: the only component that reads customers, invoices and KB articles (D18, D21)."""
import json
import re
from pathlib import Path

from pydantic import TypeAdapter

from ticketflow.models import Customer, Invoice, KbArticle, Ticket

SEED_DIR = Path(__file__).parent / "seed"

_STOPWORDS = frozenset(
    "the and for you your our are can with this that was were has have not why what how use".split()
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3 and t not in _STOPWORDS}


def _load(path: Path, model: type) -> list:
    try:
        return TypeAdapter(list[model]).validate_python(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:  # pydantic ValidationError and JSONDecodeError are ValueErrors
        raise ValueError(f"invalid seed file {path.name} ({path}): {exc}") from exc


class DataRepo:
    def __init__(
        self,
        customers: list[Customer],
        invoices: list[Invoice],
        articles: list[KbArticle],
        tickets: list[Ticket] | None = None,
    ):
        self._customers = {c.customer_id: c for c in customers}
        self._invoices = list(invoices)
        self._articles = list(articles)
        self._tickets = list(tickets or [])

    @classmethod
    def from_seed(cls, seed_dir: Path | None = None) -> "DataRepo":
        seed_dir = Path(seed_dir) if seed_dir is not None else SEED_DIR
        return cls(
            customers=_load(seed_dir / "customers.json", Customer),
            invoices=_load(seed_dir / "invoices.json", Invoice),
            articles=_load(seed_dir / "kb.json", KbArticle),
            tickets=_load(seed_dir / "tickets.json", Ticket),
        )

    def get_customer(self, customer_id: str) -> Customer | None:
        return self._customers.get(customer_id)

    def get_invoices(self, customer_id: str) -> list[Invoice]:
        return [i for i in self._invoices if i.customer_id == customer_id]

    def search_kb(self, query: str, limit: int = 3) -> list[KbArticle]:
        """Token-overlap ranking: score = shared distinct tokens with title + text; score > 0 only."""
        wanted = _tokens(query)
        scored = [(len(wanted & _tokens(f"{a.title} {a.text}")), a) for a in self._articles]
        hits = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1].article_id))
        return [a for _, a in hits[:limit]]

    def known_article_ids(self) -> list[str]:
        return [a.article_id for a in self._articles]

    def tickets(self) -> list[Ticket]:
        return list(self._tickets)

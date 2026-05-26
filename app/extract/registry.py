"""Whitelist of extractable datasets.

The registry is the *only* place where user-supplied filter input meets a SQL
query. Every dataset declares exactly which fields can be filtered, which are
required, and what the keyset sort key is. The router and worker never build
SQL by hand — they ask the registry.

Adding a new dataset = add an entry here. Nothing else changes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ExtractValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Dataset:
    name: str
    table: str
    columns: list[str]
    sort_columns: list[str]  # keyset; must be indexed in source DB
    required_filters: list[str]
    optional_filters: list[str]
    # filters that may be a list (IN clause). All others are scalars.
    list_filters: set[str] = field(default_factory=set)
    # Only required if `from`/`to` appear in required_filters or optional_filters.
    time_column: str | None = None

    @property
    def allowed_filters(self) -> set[str]:
        return set(self.required_filters) | set(self.optional_filters)

    def __post_init__(self) -> None:
        if ("from" in self.allowed_filters or "to" in self.allowed_filters) and not self.time_column:
            raise ValueError(
                f"dataset '{self.name}' declares from/to filter but no time_column"
            )


# --- Filter validation ---------------------------------------------------------

def _parse_dt(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            # accept "YYYY-MM-DDTHH:MM:SS[.ffffff][Z]"
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExtractValidationError(f"{field_name}: invalid datetime '{value}'") from exc
    raise ExtractValidationError(f"{field_name}: must be ISO datetime string")


def validate_filters(ds: Dataset, raw: dict[str, Any]) -> dict[str, Any]:
    unknown = set(raw) - ds.allowed_filters
    if unknown:
        raise ExtractValidationError(
            f"unknown filters for dataset '{ds.name}': {sorted(unknown)}"
        )
    missing = [k for k in ds.required_filters if k not in raw]
    if missing:
        raise ExtractValidationError(
            f"missing required filters for dataset '{ds.name}': {missing}"
        )

    clean: dict[str, Any] = {}
    for k, v in raw.items():
        if k in ("from", "to"):
            clean[k] = _parse_dt(v, k)
        elif k in ds.list_filters:
            if not isinstance(v, list) or not v:
                raise ExtractValidationError(f"{k}: must be a non-empty list")
            clean[k] = v
        else:
            clean[k] = v

    if "from" in clean and "to" in clean and clean["from"] >= clean["to"]:
        raise ExtractValidationError("'from' must be strictly before 'to'")
    return clean


# --- The actual datasets -------------------------------------------------------

EVENTS = Dataset(
    name="events",
    table="events",
    columns=["id", "occurred_at", "category", "user_id", "payload"],
    sort_columns=["occurred_at", "id"],
    time_column="occurred_at",
    required_filters=["from", "to"],
    optional_filters=["category", "user_id"],
    list_filters={"category", "user_id"},
)

ORDERS = Dataset(
    name="orders",
    table="orders",
    columns=[...],
    sort_columns=["id"],            # id만으로 keyset
    time_column="ordered_at",
    required_filters=[],
    optional_filters=["from", "to", "status"],
)

COUNTRIES = Dataset(
    name="countries",
    table="countries",
    columns=["code", "name", "region"],
    sort_columns=["code"],
    required_filters=[],
    optional_filters=["region"],
    # time_column 생략 — 없어도 OK
)


REGISTRY: dict[str, Dataset] = {
    EVENTS.name: EVENTS,
}


def get(name: str) -> Dataset:
    try:
        return REGISTRY[name]
    except KeyError as exc:
        raise ExtractValidationError(f"unknown dataset '{name}'") from exc

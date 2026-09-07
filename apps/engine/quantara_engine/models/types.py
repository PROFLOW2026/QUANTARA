"""PostgreSQL native enum column helper for existing DB types."""

from enum import Enum

from sqlalchemy import Enum as SAEnum


def pg_enum(enum_class: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_class,
        name=name,
        create_type=False,
        values_callable=lambda members: [member.value for member in members],
    )

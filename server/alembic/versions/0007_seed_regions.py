"""seed regions: full Russian regions catalogue from frontend layout

Revision ID: 0007_seed_regions
Revises: 0006_seed_destinations
Create Date: 2026-10-16 15:00:00.000000

Inserts every Russian region used by the frontend map (see
``frontend/static/regions-layout.json``) so a fresh deployment shows a
fully populated map instead of an empty grid. The list mirrors the
``code``/``title`` pair of every entry in that file; for the ten
regions that ship a short ``db_code`` (RU-MOW→MS, RU-SPE→SP, …,
RU-PRI→PK) we keep the short code so the existing ``PROBE_REGION``
environment values used by deployed probes keep matching without
operator action — see commit 5106bbd for the rationale.

All inserts are idempotent (``ON CONFLICT (code) DO NOTHING``) so
re-running the migration or applying it after the operator renamed
or pruned rows is a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_seed_regions"
down_revision: str | None = "0006_seed_destinations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_REGIONS: tuple[tuple[str, str], ...] = (
    ("SP", "Санкт-Петербург"),
    ("RU-MUR", "Мурманская область"),
    ("MS", "Москва"),
    ("RU-KR", "Республика Карелия"),
    ("RU-NEN", "Ненецкий автономный округ"),
    ("RU-CHU", "Чукотский автономный округ"),
    ("RU-KAM", "Камчатский край"),
    ("RU-LEN", "Ленинградская область"),
    ("RU-NGR", "Новгородская область"),
    ("RU-VLG", "Вологодская область"),
    ("RU-ARK", "Архангельская область"),
    ("RU-KO", "Республика Коми"),
    ("RU-YAN", "Ямало-Ненецкий автономный округ"),
    ("RU-KYA", "Красноярский край"),
    ("RU-SA", "Республика Саха (Якутия)"),
    ("RU-MAG", "Магаданская область"),
    ("RU-KGD", "Калининградская область"),
    ("RU-PSK", "Псковская область"),
    ("RU-TVE", "Тверская область"),
    ("RU-YAR", "Ярославская область"),
    ("RU-IVA", "Ивановская область"),
    ("RU-KOS", "Костромская область"),
    ("RU-ME", "Республика Марий Эл"),
    ("RU-KIR", "Кировская область"),
    ("RU-PER", "Пермский край"),
    ("RU-KHM", "Ханты-Мансийский автономный округ — Югра"),
    ("RU-TYU", "Тюменская область"),
    ("RU-TOM", "Томская область"),
    ("RU-KEM", "Кемеровская область"),
    ("RU-IRK", "Иркутская область"),
    ("RU-AMU", "Амурская область"),
    ("HA", "Хабаровский край"),
    ("RU-SAK", "Сахалинская область"),
    ("RU-SMO", "Смоленская область"),
    ("RU-KLU", "Калужская область"),
    ("RU-MOS", "Московская область"),
    ("RU-VLA", "Владимирская область"),
    ("NN", "Нижегородская область"),
    ("RU-CU", "Чувашская Республика"),
    ("KZN", "Республика Татарстан"),
    ("RU-UD", "Удмуртская Республика"),
    ("YE", "Свердловская область"),
    ("RU-KGN", "Курганская область"),
    ("NS", "Новосибирская область"),
    ("RU-KK", "Республика Хакасия"),
    ("RU-BU", "Республика Бурятия"),
    ("RU-YEV", "Еврейская автономная область"),
    ("RU-BRY", "Брянская область"),
    ("RU-ORL", "Орловская область"),
    ("RU-TUL", "Тульская область"),
    ("RU-RYA", "Рязанская область"),
    ("RU-MO", "Республика Мордовия"),
    ("RU-ULY", "Ульяновская область"),
    ("RU-SAM", "Самарская область"),
    ("RU-BA", "Республика Башкортостан"),
    ("RU-CHE", "Челябинская область"),
    ("RU-OMS", "Омская область"),
    ("RU-AL", "Республика Алтай"),
    ("RU-TY", "Республика Тыва"),
    ("RU-ZAB", "Забайкальский край"),
    ("PK", "Приморский край"),
    ("RU-KUR", "Курская область"),
    ("RU-LIP", "Липецкая область"),
    ("RU-TAM", "Тамбовская область"),
    ("RU-PNZ", "Пензенская область"),
    ("RU-SAR", "Саратовская область"),
    ("RU-ORE", "Оренбургская область"),
    ("RU-ALT", "Алтайский край"),
    ("RU-DON", "Донецкая Народная Республика"),
    ("RU-LUG", "Луганская Народная Республика"),
    ("RU-BEL", "Белгородская область"),
    ("RU-VOR", "Воронежская область"),
    ("RU-VGG", "Волгоградская область"),
    ("RU-KHE", "Херсонская область"),
    ("RU-ZAP", "Запорожская область"),
    ("RU-CRI", "Республика Крым"),
    ("RU-AD", "Республика Адыгея"),
    ("KD", "Краснодарский край"),
    ("RO", "Ростовская область"),
    ("RU-KL", "Республика Калмыкия"),
    ("RU-AST", "Астраханская область"),
    ("RU-SEV", "Севастополь"),
    ("RU-KC", "Карачаево-Черкесская Республика"),
    ("RU-STA", "Ставропольский край"),
    ("RU-CE", "Чеченская Республика"),
    ("RU-DA", "Республика Дагестан"),
    ("RU-KB", "Кабардино-Балкарская Республика"),
    ("RU-SE", "Республика Северная Осетия — Алания"),
    ("RU-IN", "Республика Ингушетия"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for code, title in _REGIONS:
        bind.exec_driver_sql(
            "INSERT INTO regions (code, title) VALUES ($1, $2) ON CONFLICT (code) DO NOTHING",
            (code, title),
        )


def downgrade() -> None:
    bind = op.get_bind()
    codes = [code for code, _ in _REGIONS]
    bind.exec_driver_sql(
        "DELETE FROM regions WHERE code = ANY($1::text[])",
        (codes,),
    )

TECHS = ["solar", "battery", "wind", "tline", "substation"]
TABLE_SUFFIX_ORDER = ["pre", "assumption", "model"]

# All 13 tables in dependency order
def get_tables_for_tech(tech: str) -> list[str]:
    return [f"{tech}_{suffix}" for suffix in TABLE_SUFFIX_ORDER]

# Date-range chunks: split 3 years into quarters
DATE_CHUNKS = [
    ("2023-01-01", "2023-04-01"),
    ("2023-04-01", "2023-07-01"),
    ("2023-07-01", "2023-10-01"),
    ("2023-10-01", "2024-01-01"),
    ("2024-01-01", "2024-04-01"),
    ("2024-04-01", "2024-07-01"),
    ("2024-07-01", "2024-10-01"),
    ("2024-10-01", "2025-01-01"),
    ("2025-01-01", "2025-04-01"),
    ("2025-04-01", "2025-07-01"),
    ("2025-07-01", "2025-10-01"),
    ("2025-10-01", "2026-02-21"),  # up to prod cutoff
]

BATCH_SIZE = 5_000          # rows per keyset page
PRIMARY_KEY = {             # primary key column per table (adjust to your schema)
    "solar_pre":              "solar_pre_id",
    "solar_assumption":       "solar_assumption_id",
    "solar_model":            "solar_model_id",
    "battery_pre":            "battery_pre_id",
}
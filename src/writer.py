from psycopg2.extras import execute_values

def bulk_upsert(prod_conn, table: str, rows: list, cols: list, primary_key: str):
    """
    Uses PostgreSQL bulk insert with conflict handling on primary key.
    """
    placeholders = ", ".join(["%s"] * len(cols))
    col_list = ", ".join(cols)
    update_cols = [c for c in cols if c != primary_key]
    update_clause = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])

    sql = f"""
        INSERT INTO {table} ({col_list})
        VALUES %s
        ON CONFLICT ({primary_key}) DO UPDATE SET {update_clause}
    """

    with prod_conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=1000)
    prod_conn.commit()
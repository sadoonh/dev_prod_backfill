def read_batches(
    dev_conn,
    table: str,
    natural_key: str,
    date_from: str,
    date_to: str,
    batch_size: int,
):
    """
    Yields batches of rows using keyset pagination within a date range.
    Never uses OFFSET — safe for 50M+ row tables.
    """
    last_key = None

    while True:
        if last_key is None:
            query = f"""
                SELECT * FROM {table}
                WHERE created_at >= %s AND created_at < %s
                ORDER BY {natural_key} ASC
                LIMIT %s
            """
            params = (date_from, date_to, batch_size)
        else:
            query = f"""
                SELECT * FROM {table}
                WHERE created_at >= %s AND created_at < %s
                  AND {natural_key} > %s
                ORDER BY {natural_key} ASC
                LIMIT %s
            """
            params = (date_from, date_to, last_key, batch_size)

        with dev_conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            cols = [desc.name for desc in cur.description]

        if not rows:
            break

        yield rows, cols
        last_key = rows[-1][cols.index(natural_key)]

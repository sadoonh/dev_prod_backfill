import concurrent.futures
import logging
import os
import psycopg2
from config import TECHS, DATE_CHUNKS, BATCH_SIZE, PRIMARY_KEY, get_tables_for_tech
from checkpoint import load_state, mark_chunk_done, is_chunk_done
from reader import read_batches
from writer import bulk_upsert

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


def backfill_tech(tech: str, state: dict, dry_run: bool = False):
    dev_conn = psycopg2.connect(os.environ["DEV_DATABASE_URL"])
    prod_conn = psycopg2.connect(os.environ["PROD_DATABASE_URL"])

    for table in get_tables_for_tech(tech):  # pre → assumption → model
        pk = PRIMARY_KEY[table]

        for date_from, date_to in DATE_CHUNKS:
            chunk_key = f"{date_from}__{date_to}"

            if is_chunk_done(state, table, chunk_key):
                logging.info(f"SKIP {table} [{chunk_key}] — already done")
                continue

            total = 0
            for rows, cols in read_batches(
                dev_conn, table, pk, date_from, date_to, BATCH_SIZE
            ):
                if not dry_run:
                    bulk_upsert(prod_conn, table, rows, cols, pk)
                total += len(rows)
                logging.info(
                    f"{'[DRY]' if dry_run else ''} "
                    f"{table} [{chunk_key}] +{len(rows)} rows "
                    f"(total this chunk: {total})"
                )

            if not dry_run:
                mark_chunk_done(state, table, chunk_key)

        logging.info(f"{table} complete")

    dev_conn.close()
    prod_conn.close()


def main(dry_run: bool = False, techs: list = None):
    state = load_state()
    target_techs = techs or TECHS

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_techs)) as ex:
        futures = {ex.submit(backfill_tech, t, state, dry_run): t for t in target_techs}
        for f in concurrent.futures.as_completed(futures):
            tech = futures[f]
            try:
                f.result()
                logging.info(f"Tech [{tech}] fully complete")
            except Exception as e:
                logging.error(f"Tech [{tech}] failed: {e}")
                raise


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--tech", nargs="+", choices=TECHS, default=None)
    args = p.parse_args()
    main(dry_run=args.dry_run, techs=args.tech)

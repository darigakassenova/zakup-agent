# Подключение к базе и выполнение запросов
import os
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from ingest import DEFAULT_DSN


@dataclass
class Context:
    conn: psycopg.Connection
    lang: str = "ru"

    def rows(self, sql, params=None):
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()

    def one(self, sql, params=None):
        found = self.rows(sql, params)
        return found[0] if found else None


def connect():
    return psycopg.connect(os.getenv("DATABASE_URL", DEFAULT_DSN), autocommit=True,
                           options="-c default_transaction_read_only=on")
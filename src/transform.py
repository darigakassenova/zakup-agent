import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from ingest import DEFAULT_DSN

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "sql" / "marts.sql"

QUALITY_CHECKS = [
    ("договоров в базе", "SELECT count(*) FROM contract"),
    ("актуальных договоров (последняя версия)", "SELECT count(*) FROM mart.contract_current"),
    ("  договоров без позиций", "SELECT count(*) FROM contract c "
                                "WHERE NOT EXISTS (SELECT 1 FROM contract_unit u WHERE u.contract_id = c.id)"),
    ("строк в таблице цен", "SELECT count(*) FROM mart.price_fact"),
    ("  с нормальной ценой", "SELECT count(*) FROM mart.price_fact WHERE price_ok"),
    ("  с нулевым или отрицательным количеством", "SELECT count(*) FROM mart.price_fact WHERE NOT qty_ok"),
    ("  с несходящейся суммой", "SELECT count(*) FROM mart.price_fact WHERE NOT sum_ok"),
    ("  без кода ЕНСТРУ", "SELECT count(*) FROM mart.price_fact WHERE enstru_code IS NULL"),
    ("  без единицы измерения", "SELECT count(*) FROM mart.price_fact WHERE base_unit IS NULL"),
    ("  пригодных для эталона цен", "SELECT count(*) FROM mart.price_fact WHERE is_benchmark"),
    ("  с известным городом", "SELECT count(*) FROM mart.price_fact WHERE city_code IS NOT NULL"),
    ("    из них из плана", "SELECT count(*) FROM mart.price_fact WHERE city_source = 'plan'"),
    ("групп «товар + единица»", "SELECT count(*) FROM mart.price_benchmark"),
    ("  из них с 5 и более позициями", "SELECT count(*) FROM mart.price_benchmark WHERE n >= 5"),
    ("отклонение цены больше 30% (в группах от 5 позиций)",
     "SELECT count(*) FROM mart.price_deviation WHERE n >= 5 AND abs(deviation) > 0.3"),
    ("последнее обновление данных из API", "SELECT max(updated_at) FROM etl_state"),
]


def main():
    load_dotenv(ROOT / ".env")
    with psycopg.connect(os.getenv("DATABASE_URL", DEFAULT_DSN)) as conn:
        conn.execute(SQL_FILE.read_text(encoding="utf-8"))
        conn.commit()
        for title, query in QUALITY_CHECKS:
            print(f"{title}: {conn.execute(query).fetchone()[0]}")


if __name__ == "__main__":
    main()
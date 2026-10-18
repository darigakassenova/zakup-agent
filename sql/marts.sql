CREATE SCHEMA IF NOT EXISTS mart;

DROP VIEW IF EXISTS mart.volume_anomaly;
DROP MATERIALIZED VIEW IF EXISTS mart.volume_year;
DROP VIEW IF EXISTS mart.price_deviation;
DROP VIEW IF EXISTS mart.lot_clean;
DROP MATERIALIZED VIEW IF EXISTS mart.price_benchmark;
DROP MATERIALIZED VIEW IF EXISTS mart.price_fact;
DROP VIEW IF EXISTS mart.contract_current;
DROP TABLE IF EXISTS mart.unit_norm;
DROP FUNCTION IF EXISTS mart.city_code(text);
DROP FUNCTION IF EXISTS mart.clean_name(text);


CREATE TABLE mart.unit_norm (
    code      text PRIMARY KEY,
    base_unit text    NOT NULL,
    factor    numeric NOT NULL
);

INSERT INTO mart.unit_norm (code, base_unit, factor) VALUES
    ('796', 'шт', 1), ('797', 'шт', 100), ('642', 'шт', 1),
    ('166', 'кг', 1), ('163', 'кг', 0.001), ('168', 'кг', 1000),
    ('112', 'л', 1), ('111', 'л', 0.001), ('116', 'л', 10), ('122', 'л', 100), ('113', 'л', 1000),
    ('006', 'м', 1), ('018', 'м', 1), ('5156', 'м', 1), ('001', 'м', 0.000000001),
    ('055', 'м2', 1), ('081', 'м2', 1),
    ('5114', 'услуга', 1), ('980', 'работа', 1),
    ('778', 'упаковка', 1), ('5111', 'пачка', 1), ('872', 'флакон', 1), ('868', 'бутылка', 1),
    ('736', 'рулон', 1), ('870', 'ампула', 1), ('625', 'лист', 1), ('5108', 'баллон', 1),
    ('616', 'бобина', 1), ('715', 'пара', 1), ('839', 'комплект', 1), ('704', 'набор', 1),
    ('639', 'доза', 1), ('245', 'кВт·ч', 1), ('214', 'кВт', 1), ('365', 'полугодие', 1),
    ('881', 'банка усл.', 1), ('879', 'шт усл.', 1), ('889', 'катушка усл.', 1),
    ('876', 'ед. усл.', 1), ('062', 'м2 усл.', 1), ('179', 'т усл.', 1),
    ('972', 'шт в 20-фут. экв.', 1), ('547', 'пар в смену', 1), ('316', 'кг/м3', 1);


CREATE FUNCTION mart.city_code(kato text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN kato IS NULL OR length(kato) <> 9 THEN NULL
        WHEN left(kato, 2) IN ('71', '75', '79') THEN left(kato, 2) || '0000000'
        ELSE left(kato, 4) || '00000'
    END
$$;

CREATE FUNCTION mart.clean_name(name text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT btrim(regexp_replace(
        translate(lower(replace(name, chr(160), ' ')), 'ё«»“”„"''`', 'е'),
        '\s+', ' ', 'g'))
$$;


CREATE VIEW mart.contract_current AS
SELECT latest.*,
       CASE
           WHEN status_id IN (340, 350) THEN 'terminated'
           WHEN status_id IN (450, 455) THEN 'changed'
           WHEN status_id IN (456, 460, 464) THEN 'transferred'
           ELSE 'active'
       END AS status_group
FROM (
    SELECT DISTINCT ON (coalesce(root_id, id)) *
    FROM contract
    WHERE NOT coalesce(deleted, false)
    ORDER BY coalesce(root_id, id), id DESC
) latest;


CREATE MATERIALIZED VIEW mart.price_fact AS
WITH plan_place AS (
    SELECT DISTINCT ON (plan_id) plan_id, kato_code, place_name
    FROM plan_kato
    ORDER BY plan_id, count DESC NULLS LAST, kato_code
),
customer_city AS (
    SELECT customer_bin, city_code
    FROM (
        SELECT p.customer_bin,
               mart.city_code(k.kato_code) AS city_code,
               row_number() OVER (PARTITION BY p.customer_bin
                                  ORDER BY count(*) DESC, mart.city_code(k.kato_code)) AS rn
        FROM plan p JOIN plan_kato k ON k.plan_id = p.id
        WHERE mart.city_code(k.kato_code) IS NOT NULL
        GROUP BY p.customer_bin, mart.city_code(k.kato_code)
    ) ranked
    WHERE rn = 1
),
base AS (
    SELECT cu.id AS unit_id,
           c.id AS contract_id,
           c.customer_bin,
           c.supplier_bin,
           c.sign_date,
           c.status_group,
           cu.enstru_code,
           e.name_ru AS enstru_name,
           cu.plan_id,
           p.name_ru AS item_name,
           p.price AS plan_price,
           cu.quantity,
           cu.item_price,
           cu.item_price_wnds,
           cu.total_sum,
           coalesce(nullif(cu.item_price_wnds, 0), cu.item_price) AS unit_price,
           coalesce(un.base_unit, lower(ru.name_ru), p.units_code) AS base_unit,
           coalesce(un.factor, 1) AS unit_factor,
           pp.kato_code,
           pp.place_name,
           coalesce(mart.city_code(pp.kato_code), cc.city_code) AS city_code,
           CASE WHEN pp.kato_code IS NOT NULL THEN 'plan'
                WHEN cc.city_code IS NOT NULL THEN 'customer'
           END AS city_source
    FROM contract_unit cu
    JOIN mart.contract_current c ON c.id = cu.contract_id
    LEFT JOIN plan p ON p.id = cu.plan_id
    LEFT JOIN ref_enstru e ON e.code = cu.enstru_code
    LEFT JOIN ref_units ru ON ru.code = p.units_code
    LEFT JOIN mart.unit_norm un ON un.code = p.units_code
    LEFT JOIN plan_place pp ON pp.plan_id = cu.plan_id
    LEFT JOIN customer_city cc ON cc.customer_bin = c.customer_bin
    WHERE NOT coalesce(cu.deleted, false) AND NOT coalesce(cu.cr_deleted, false)
),
checked AS (
    SELECT base.*,
           extract(year FROM sign_date)::int AS year,
           extract(month FROM sign_date)::int AS month,
           unit_price / unit_factor AS price_per_base,
           quantity * unit_factor AS qty_base,
           unit_price > 0 AND unit_price <> 1 AS price_ok,
           quantity > 0 AS qty_ok,
           total_sum IS NULL OR total_sum = 0
               OR least(abs(item_price * quantity - total_sum),
                        abs(coalesce(item_price_wnds, item_price) * quantity - total_sum))
                  <= greatest(1, 0.005 * total_sum) AS sum_ok
    FROM base
)
SELECT checked.*,
       price_ok AND qty_ok AND sum_ok AND base_unit IS NOT NULL AND enstru_code IS NOT NULL
           AND status_group IN ('active', 'transferred') AS is_benchmark
FROM checked;

CREATE INDEX ON mart.price_fact (enstru_code, base_unit);
CREATE INDEX ON mart.price_fact (customer_bin, sign_date);
CREATE INDEX ON mart.price_fact (city_code);
CREATE UNIQUE INDEX ON mart.price_fact (unit_id);


CREATE MATERIALIZED VIEW mart.price_benchmark AS
SELECT enstru_code,
       base_unit,
       max(enstru_name) AS enstru_name,
       count(*) AS n,
       count(DISTINCT customer_bin) AS customers,
       count(DISTINCT supplier_bin) AS suppliers,
       sum(price_per_base * qty_base) / sum(qty_base) AS weighted_avg,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_base) AS median_price,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY price_per_base) AS q1,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY price_per_base) AS q3,
       min(price_per_base) AS min_price,
       max(price_per_base) AS max_price,
       min(sign_date) AS first_date,
       max(sign_date) AS last_date
FROM mart.price_fact
WHERE is_benchmark
GROUP BY enstru_code, base_unit;

CREATE UNIQUE INDEX ON mart.price_benchmark (enstru_code, base_unit);


CREATE VIEW mart.price_deviation AS
WITH grouped AS (
    SELECT f.*,
           sum(price_per_base * qty_base) OVER w AS group_sum,
           sum(qty_base) OVER w AS group_qty,
           count(*) OVER w AS group_n
    FROM mart.price_fact f
    WHERE is_benchmark
    WINDOW w AS (PARTITION BY enstru_code, base_unit)
),
compared AS (
    SELECT grouped.*,
           (group_sum - price_per_base * qty_base) / nullif(group_qty - qty_base, 0) AS ref_weighted_avg
    FROM grouped
)
SELECT unit_id, contract_id, customer_bin, supplier_bin, sign_date, enstru_code, enstru_name,
       item_name, base_unit, quantity, qty_base, price_per_base, ref_weighted_avg,
       group_n AS n,
       group_n - 1 AS ref_n,
       price_per_base / nullif(ref_weighted_avg, 0) - 1 AS deviation,
       city_code, city_source
FROM compared;


CREATE VIEW mart.lot_clean AS
SELECT l.id AS lot_id,
       l.customer_bin,
       l.trd_buy_id,
       l.name_ru,
       mart.clean_name(l.name_ru) AS name_norm,
       l.count,
       l.amount,
       t.publish_date,
       extract(year FROM t.publish_date)::int AS year
FROM lot l
LEFT JOIN trd_buy t ON t.id = l.trd_buy_id;


CREATE MATERIALIZED VIEW mart.volume_year AS
WITH planned AS (
    SELECT p.customer_bin,
           p.enstru_code,
           coalesce(un.base_unit, lower(ru.name_ru), p.units_code) AS base_unit,
           p.year,
           sum(p.count * coalesce(un.factor, 1)) AS plan_qty,
           sum(p.amount) AS plan_amount,
           count(*) AS plan_items,
           array_agg(p.id ORDER BY p.amount DESC NULLS LAST) AS plan_ids
    FROM plan p
    JOIN subject s ON s.bin = p.customer_bin AND s.is_target
    LEFT JOIN mart.unit_norm un ON un.code = p.units_code
    LEFT JOIN ref_units ru ON ru.code = p.units_code
    WHERE p.is_active AND NOT coalesce(p.is_deleted, false)
      AND p.enstru_code IS NOT NULL AND p.count > 0 AND p.year BETWEEN 2024 AND 2026
    GROUP BY 1, 2, 3, 4
),
contracted AS (
    SELECT customer_bin, enstru_code, base_unit, year,
           sum(qty_base) AS contract_qty,
           sum(unit_price * quantity) AS contract_amount,
           count(DISTINCT contract_id) AS contracts
    FROM mart.price_fact
    WHERE status_group IN ('active', 'transferred') AND qty_ok AND enstru_code IS NOT NULL AND base_unit IS NOT NULL
    GROUP BY 1, 2, 3, 4
)
SELECT coalesce(p.customer_bin, c.customer_bin) AS customer_bin,
       coalesce(p.enstru_code, c.enstru_code) AS enstru_code,
       coalesce(p.base_unit, c.base_unit) AS base_unit,
       coalesce(p.year, c.year) AS year,
       e.name_ru AS enstru_name,
       p.plan_qty, p.plan_amount, p.plan_items, p.plan_ids,
       c.contract_qty, c.contract_amount, c.contracts
FROM planned p
FULL JOIN contracted c
       ON c.customer_bin = p.customer_bin AND c.enstru_code = p.enstru_code
      AND c.base_unit = p.base_unit AND c.year = p.year
LEFT JOIN ref_enstru e ON e.code = coalesce(p.enstru_code, c.enstru_code);

CREATE INDEX ON mart.volume_year (customer_bin, enstru_code, base_unit, year);


CREATE VIEW mart.volume_anomaly AS
WITH history AS (
    SELECT v.*,
           avg(plan_qty) OVER w AS baseline_qty,
           count(plan_qty) OVER w AS baseline_years,
           avg(contract_qty) OVER w AS baseline_contract_qty
    FROM mart.volume_year v
    WINDOW w AS (PARTITION BY customer_bin, enstru_code, base_unit ORDER BY year
                 RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
),
ratios AS (
    SELECT history.*,
           plan_qty / nullif(baseline_qty, 0) AS ratio,
           ln(plan_qty / nullif(baseline_qty, 0)) AS log_ratio
    FROM history
    WHERE plan_qty > 0
),
spread AS (
    SELECT year,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY log_ratio) AS median_log_ratio,
           count(*) AS compared
    FROM ratios
    WHERE log_ratio IS NOT NULL
    GROUP BY year
),
mad AS (
    SELECT r.year,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(r.log_ratio - s.median_log_ratio)) AS mad
    FROM ratios r JOIN spread s USING (year)
    WHERE r.log_ratio IS NOT NULL
    GROUP BY r.year
)
SELECT r.customer_bin, r.enstru_code, r.enstru_name, r.base_unit, r.year,
       r.plan_qty, r.baseline_qty, r.baseline_years, r.ratio,
       r.plan_amount,
       (r.plan_qty - r.baseline_qty) * r.plan_amount / nullif(r.plan_qty, 0) AS extra_amount,
       r.contract_qty, r.baseline_contract_qty, r.contracts, r.plan_ids,
       0.6745 * (r.log_ratio - s.median_log_ratio) / nullif(m.mad, 0) AS robust_z,
       s.compared,
       CASE
           WHEN r.baseline_qty IS NULL THEN 'no_history'
           WHEN r.ratio >= 1.5 AND 0.6745 * (r.log_ratio - s.median_log_ratio) / nullif(m.mad, 0) >= 3.5 THEN 'high'
           WHEN r.ratio >= 1.5 THEN 'elevated'
           ELSE 'normal'
       END AS flag
FROM ratios r
LEFT JOIN spread s USING (year)
LEFT JOIN mad m USING (year);

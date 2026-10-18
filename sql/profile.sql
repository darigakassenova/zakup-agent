\pset pager off
\echo '=== 1. Планы по годам и принадлежности ==='
SELECT year, (customer_bin IN (SELECT bin FROM customer)) AS наша_организация, count(*) AS планов
FROM plan GROUP BY 1, 2 ORDER BY 1, 2;

\echo '=== 2. Плановые записи-заглушки ==='
SELECT count(*) AS всего,
       count(*) FILTER (WHERE price = 1) AS цена_равна_1,
       count(*) FILTER (WHERE price = 1 AND count = 1) AS цена_и_количество_равны_1,
       count(*) FILTER (WHERE price IS NULL OR price <= 0) AS цена_пустая_или_нулевая,
       count(*) FILTER (WHERE is_active IS NOT TRUE OR is_deleted IS TRUE) AS неактуальные
FROM plan;

\echo '=== 3. Единицы измерения в планах (топ 15) ==='
SELECT p.units_code, u.name_ru, count(*) AS планов
FROM plan p LEFT JOIN ref_units u ON u.code = p.units_code
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo '=== 4. Единица измерения пустая или не из справочника ==='
SELECT count(*) FILTER (WHERE units_code IS NULL) AS пустая,
       count(*) FILTER (WHERE units_code IS NOT NULL AND units_code NOT IN (SELECT code FROM ref_units)) AS нет_в_справочнике
FROM plan;

\echo '=== 5. Регион поставки ==='
SELECT count(*) AS планов,
       count(*) FILTER (WHERE id IN (SELECT plan_id FROM plan_kato)) AS с_регионом
FROM plan;

\echo '=== 6. Справочник товаров: заполненность единицы ==='
SELECT count(*) AS позиций,
       count(*) FILTER (WHERE coalesce(edizm, '') = '') AS без_edizm,
       count(*) FILTER (WHERE coalesce(mkei, '') = '') AS без_mkei
FROM ref_enstru;

\echo '=== 7. Договоры: версии и статусы ==='
SELECT status_id, deleted, (parent_id IS NOT NULL) AS есть_parent, (root_id IS NOT NULL) AS есть_root, count(*) AS договоров
FROM contract GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 15;

\echo '=== 8. Договоры по годам подписания ==='
SELECT extract(year FROM sign_date) AS год, count(*) AS договоров, round(sum(sum) / 1e9, 1) AS сумма_млрд
FROM contract GROUP BY 1 ORDER BY 1;

\echo '=== 9. Строки договоров: цены и количества ==='
SELECT min(item_price) AS мин_цена, percentile_cont(0.5) WITHIN GROUP (ORDER BY item_price) AS медиана_цены,
       max(item_price) AS макс_цена,
       count(*) FILTER (WHERE item_price = 1) AS цена_1,
       count(*) FILTER (WHERE quantity <= 0) AS количество_нулевое,
       count(*) FILTER (WHERE abs(item_price * quantity - total_sum) > 1) AS сумма_не_сходится
FROM contract_unit;

\echo '=== 10. Совпадение цены в договоре и в плане ==='
SELECT count(*) AS строк,
       count(*) FILTER (WHERE abs(cu.item_price - p.price) < 0.01) AS цена_совпадает,
       count(*) FILTER (WHERE cu.item_price > p.price * 1.3) AS дороже_плана_на_30_проц,
       count(*) FILTER (WHERE cu.item_price < p.price * 0.7) AS дешевле_плана_на_30_проц
FROM contract_unit cu JOIN plan p ON p.id = cu.plan_id WHERE p.price > 1;

\echo '=== 11. Самые частые товары в договорах ==='
SELECT cu.enstru_code, left(e.name_ru, 60) AS товар, count(*) AS строк, count(DISTINCT c.customer_bin) AS заказчиков
FROM contract_unit cu JOIN contract c ON c.id = cu.contract_id
LEFT JOIN ref_enstru e ON e.code = cu.enstru_code
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;

\echo '=== 12. Лоты: образцы названий ==='
SELECT left(name_ru, 90) AS название, count FROM lot ORDER BY random() LIMIT 12;

\echo '=== 13. Лоты со ссылкой на планы ==='
SELECT count(*) AS лотов,
       count(*) FILTER (WHERE jsonb_array_length(plan_ids) > 0) AS со_ссылкой_на_план,
       count(*) FILTER (WHERE jsonb_array_length(plan_ids) > 1) AS с_несколькими_планами
FROM lot;
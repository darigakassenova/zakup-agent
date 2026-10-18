\pset pager off
\echo '=== 1. Все единицы измерения (справочник) ==='
SELECT u.code, u.name_ru, u.alpha_code, count(p.id) AS планов
FROM ref_units u LEFT JOIN plan p ON p.units_code = u.code
GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo '=== 2. Версии договоров ==='
SELECT count(*) AS договоров,
       count(DISTINCT root_id) AS корней,
       count(*) FILTER (WHERE id = root_id) AS id_равен_root,
       count(*) FILTER (WHERE id = parent_id) AS id_равен_parent,
       count(*) FILTER (WHERE parent_id = root_id) AS parent_равен_root
FROM contract;

\echo '=== 3. Сколько версий внутри одного корня ==='
SELECT versions, count(*) AS корней
FROM (SELECT root_id, count(*) AS versions FROM contract GROUP BY root_id) s
GROUP BY 1 ORDER BY 1;

\echo '=== 4. Пример цепочки версий ==='
SELECT id, root_id, parent_id, status_id, sign_date, sum
FROM contract
WHERE root_id IN (SELECT root_id FROM contract GROUP BY root_id HAVING count(*) > 1 LIMIT 3)
ORDER BY root_id, id;

\echo '=== 5. Версии планов ==='
SELECT count(*) AS планов,
       count(DISTINCT root_id) AS корней,
       count(*) FILTER (WHERE is_active) AS активных,
       count(*) FILTER (WHERE id = root_id) AS id_равен_root
FROM plan;

\echo '=== 6. Откуда расхождение суммы строки договора ==='
SELECT count(*) AS расходятся,
       count(*) FILTER (WHERE abs(item_price_wnds * quantity - total_sum) <= 1) AS сходится_с_НДС,
       count(*) FILTER (WHERE total_sum = 0 OR total_sum IS NULL) AS итог_нулевой
FROM contract_unit WHERE abs(item_price * quantity - total_sum) > 1;

\echo '=== 7. Образцы расхождения ==='
SELECT id, item_price, item_price_wnds, quantity, total_sum
FROM contract_unit WHERE abs(item_price * quantity - total_sum) > 1 ORDER BY random() LIMIT 8;

\echo '=== 8. Единица плана и единица в справочнике товаров ==='
SELECT count(*) AS планов_с_товаром,
       count(*) FILTER (WHERE p.units_code = e.mkei) AS совпадает,
       count(*) FILTER (WHERE p.units_code <> e.mkei) AS не_совпадает
FROM plan p JOIN ref_enstru e ON e.code = p.enstru_code;

\echo '=== 9. Самые частые несовпадения единиц ==='
SELECT p.units_code AS в_плане, e.mkei AS в_справочнике, count(*) AS планов
FROM plan p JOIN ref_enstru e ON e.code = p.enstru_code
WHERE p.units_code <> e.mkei GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10;

\echo '=== 10. Самые длинные названия лотов ==='
SELECT left(name_ru, 120) AS название FROM lot ORDER BY length(name_ru) DESC LIMIT 8;

\echo '=== 11. Лоты: что нетипичного в названиях ==='
SELECT count(*) AS лотов,
       count(*) FILTER (WHERE name_ru ~ '[0-9]') AS с_цифрами,
       count(*) FILTER (WHERE name_ru ~ '["«»()]') AS с_кавычками_скобками,
       count(*) FILTER (WHERE name_ru <> btrim(name_ru) OR name_ru ~ '  ') AS с_лишними_пробелами,
       count(*) FILTER (WHERE name_ru ~ '^[A-Za-z0-9 .,-]+$') AS латиница
FROM lot;

\echo '=== 12. Число планов в договорах и лотах на один ENSTRU ==='
SELECT count(DISTINCT enstru_code) AS кодов_товаров,
       count(*) FILTER (WHERE n >= 30) AS кодов_с_30_и_более_договорных_строк
FROM (SELECT enstru_code, count(*) AS n FROM contract_unit GROUP BY 1) s;
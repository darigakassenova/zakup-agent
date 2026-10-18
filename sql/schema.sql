CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.record (
    entity     text        NOT NULL,
    id         bigint      NOT NULL,
    payload    jsonb       NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity, id)
);

CREATE TABLE IF NOT EXISTS etl_state (
    entity          text        NOT NULL,
    bin             text        NOT NULL,
    last_index_date text,
    rows_loaded     bigint      NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity, bin)
);

CREATE TABLE IF NOT EXISTS ref_units (
    code        text PRIMARY KEY,
    name_ru     text,
    alpha_code  text
);

CREATE TABLE IF NOT EXISTS ref_enstru (
    code       text PRIMARY KEY,
    name_ru    text,
    edizm      text,
    mkei       text
);

CREATE TABLE IF NOT EXISTS ref_kato (
    code           text PRIMARY KEY,
    name_ru        text,
    full_name_ru   text,
    parent_code    text,
    level          int
);

CREATE TABLE IF NOT EXISTS ref_status (
    kind     text NOT NULL,
    id       int  NOT NULL,
    code     text,
    name_ru  text,
    name_kz  text,
    PRIMARY KEY (kind, id)
);

CREATE TABLE IF NOT EXISTS subject (
    bin        text PRIMARY KEY,
    name_ru    text,
    name_kz    text,
    is_target  boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS plan (
    id            bigint PRIMARY KEY,
    root_id       bigint,
    customer_bin  text NOT NULL,
    name_ru       text,
    extra_desc    text,
    enstru_code   text,
    units_code    text,
    price         numeric,
    count         numeric,
    amount        numeric,
    year          int,
    is_active     boolean,
    is_deleted    boolean,
    subject_type_id int,
    trade_method_id int
);
CREATE INDEX IF NOT EXISTS plan_bin_year_idx ON plan (customer_bin, year);
CREATE INDEX IF NOT EXISTS plan_enstru_idx   ON plan (enstru_code);

CREATE TABLE IF NOT EXISTS plan_kato (
    plan_id    bigint NOT NULL,
    kato_code  text   NOT NULL,
    place_name text,
    count      numeric,
    PRIMARY KEY (plan_id, kato_code)
);

CREATE TABLE IF NOT EXISTS trd_buy (
    id              bigint PRIMARY KEY,
    number_anno     text,
    org_bin         text,
    customer_bin    text,
    name_ru         text,
    publish_date    timestamp,
    fin_year        jsonb,
    status_id       int,
    trade_method_id int,
    subject_type_id int
);
CREATE INDEX IF NOT EXISTS trd_buy_bin_date_idx ON trd_buy (org_bin, publish_date);

CREATE TABLE IF NOT EXISTS lot (
    id            bigint PRIMARY KEY,
    customer_bin  text NOT NULL,
    trd_buy_id    bigint,
    name_ru       text,
    count         numeric,
    amount        numeric,
    plan_ids      jsonb,
    enstru_list   jsonb,
    kato_list     jsonb,
    lot_number    text,
    status_id     int,
    trade_method_id int
);
CREATE INDEX IF NOT EXISTS lot_trd_buy_idx ON lot (trd_buy_id);
CREATE INDEX IF NOT EXISTS lot_bin_idx     ON lot (customer_bin);
CREATE INDEX IF NOT EXISTS lot_number_idx  ON lot (lot_number);

CREATE TABLE IF NOT EXISTS contract (
    id            bigint PRIMARY KEY,
    customer_bin  text NOT NULL,
    supplier_bin  text,
    trd_buy_id    bigint,
    sign_date     date,
    sum           numeric,
    fin_year      int,
    status_id     int,
    parent_id     bigint,
    root_id       bigint,
    deleted       boolean,
    contract_number text,
    trd_buy_number  text,
    subject_type_id int,
    trade_method_id int
);
CREATE INDEX IF NOT EXISTS contract_bin_date_idx ON contract (customer_bin, sign_date);
CREATE INDEX IF NOT EXISTS contract_supplier_idx ON contract (supplier_bin);
CREATE INDEX IF NOT EXISTS contract_number_idx   ON contract (contract_number);

CREATE TABLE IF NOT EXISTS contract_unit (
    id               bigint PRIMARY KEY,
    contract_id      bigint NOT NULL,
    lot_id           bigint,
    plan_id          bigint,
    enstru_code      text,
    item_price       numeric,
    item_price_wnds  numeric,
    quantity         numeric,
    total_sum        numeric,
    deleted          boolean,
    cr_deleted       boolean
);
CREATE INDEX IF NOT EXISTS contract_unit_contract_idx ON contract_unit (contract_id);
CREATE INDEX IF NOT EXISTS contract_unit_plan_idx     ON contract_unit (plan_id);
CREATE INDEX IF NOT EXISTS contract_unit_enstru_idx   ON contract_unit (enstru_code);
-- 研報市場標籤 + 向量資料庫 schema（原型）
-- 套用：docker exec -i report-mark-postgres psql -U postgres -d research < db/schema.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS research;

-- 報告層：每檔一列，含市場標籤與檔名 metadata
CREATE TABLE IF NOT EXISTS research.research_report (
    id            uuid PRIMARY KEY,
    file_hash     text UNIQUE NOT NULL,          -- SHA256，作為去重/coupling key
    file_name     text NOT NULL,
    file_path     text NOT NULL,
    market        text,                          -- 市場標籤 enum（見 tagging.py）
    is_research   boolean,                        -- false = 行政/活動檔，不進向量檢索
    confidence    real,                           -- Claude 對市場標籤的信心 0-1
    stock_code    text,
    company_name  text,
    source        text,                           -- 券商（檔名解析）
    report_date   date,
    report_type   text,
    language      text,
    instrument_types text[],                       -- 金融商品類型（可多值，見 tagging.py）
    relates_stock    boolean,                       -- 是否與個股（選股/個股交易）相關
    relates_futures  boolean,                       -- 是否與期貨/指數部位相關
    stock_targets    text[],                        -- 個股標的代碼清單（內文抽取，見 tagging.py）
    futures_targets  text[],                        -- 期貨商品（小詞表，見 tagging.py）
    full_text        text,                          -- 報告全文（供「查看完整報告」；來源為 extracted text）
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- 分塊層：全文切塊 + 1024 維 BGE-M3 dense 向量
CREATE TABLE IF NOT EXISTS research.report_chunk (
    id           uuid PRIMARY KEY,
    report_id    uuid NOT NULL REFERENCES research.research_report(id) ON DELETE CASCADE,
    chunk_index  int  NOT NULL,
    content      text NOT NULL,
    embedding    vector(1024)
);

CREATE INDEX IF NOT EXISTS idx_report_chunk_report_id
    ON research.report_chunk (report_id);

CREATE INDEX IF NOT EXISTS idx_report_chunk_embedding
    ON research.report_chunk USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_research_report_market
    ON research.research_report (market);

-- 既有資料庫補欄（CREATE TABLE IF NOT EXISTS 不會替既有表補欄，靠 ALTER 冪等補上）
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS instrument_types text[];
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS relates_stock    boolean;
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS relates_futures  boolean;
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS stock_targets    text[];  -- 個股標的代碼清單 ["2330","2303"]
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS futures_targets  text[];  -- 期貨商品（小詞表）["台指期"]
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS full_text        text;    -- 報告全文（供「查看完整報告」）
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS summary          text;    -- 報告摘要（2-3 句，供卡片/列表/modal 預覽）

CREATE INDEX IF NOT EXISTS idx_research_report_instr
    ON research.research_report USING gin (instrument_types);
CREATE INDEX IF NOT EXISTS idx_rr_stock_targets
    ON research.research_report USING gin (stock_targets);
CREATE INDEX IF NOT EXISTS idx_rr_futures_targets
    ON research.research_report USING gin (futures_targets);

-- 混合檢索：pg_trgm 字面比對。content_norm = NFKC → 去除所有空白 → 小寫，
-- 解決 PDF 抽字的 CJK 間空白／全形字母／大小寫比對問題。
-- 表達式必須與 app/services/textnorm.py 的 norm_for_match() 一致。
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE research.report_chunk ADD COLUMN IF NOT EXISTS content_norm text
    GENERATED ALWAYS AS (lower(regexp_replace(normalize(content, NFKC), '\s+', '', 'g'))) STORED;

CREATE INDEX IF NOT EXISTS idx_report_chunk_content_trgm
    ON research.report_chunk USING gin (content_norm gin_trgm_ops);

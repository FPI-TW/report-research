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

-- 問答記錄（Phase 1 RAG）：每次 /api/ask 寫一列，供稽核/分析（冪等建表）
CREATE TABLE IF NOT EXISTS research.qa_log (
    id               uuid PRIMARY KEY,
    question         text NOT NULL,
    answer           text,
    cited_report_ids uuid[],                        -- 回答實際引用的報告 id
    filters          jsonb,                         -- 提問時套用的市場/商品/類型等篩選
    latency_ms       int,
    thinking_ms      int,                           -- 思考時間：開始→第一個 token（毫秒）
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_qa_log_created_at
    ON research.qa_log (created_at DESC);
-- 使用者對回答的回饋：'like' / 'dislike' / NULL（供日後調整答題品質；冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS feedback text;
-- 當時完整來源（含編號），供歷史重現可點 [n]（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS sources jsonb;
-- 當時外部參考（網搜結果），供歷史重現保留「外部參考」區塊（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS ext_sources jsonb;
-- 多輪對話：同一對話的多列共用此 id；NULL（舊列）視為各自獨立的單題對話（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS conversation_id uuid;
CREATE INDEX IF NOT EXISTS idx_qa_log_conversation
    ON research.qa_log ((COALESCE(conversation_id, id)), created_at);
-- 思考時間：開始→第一個 token（毫秒），供歷史顯示（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS thinking_ms int;
-- M3：版本群組鍵（重新生成的多版本共用；NULL 以自身 id 為群組，冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS root_qa_id uuid;
-- M3：有效列旗標（重生舊版/編輯截斷下游設 false；歷史/續問僅取 true，冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
-- M3：思考步驟（stage 名稱序列），供歷史重現思考卡（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS stages jsonb;
-- M3：追問建議（字串陣列），供歷史重現追問 chips（冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS followups jsonb;
-- M3：停止標記（使用者中斷串流時保存的部分答案列，冪等補欄）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS stopped boolean NOT NULL DEFAULT false;
-- 串流完成與「停止」請求共用前端 request_id，避免網路競態寫出兩筆同一輪問答。
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS request_id uuid;
CREATE UNIQUE INDEX IF NOT EXISTS idx_qa_log_request_id
    ON research.qa_log (request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_qa_log_root
    ON research.qa_log ((COALESCE(root_qa_id, id)), created_at);

-- 生成的深度研報（隨對話輪次保存；markdown 為真相來源，PDF 可由其重建）
CREATE TABLE IF NOT EXISTS research.report_doc (
    id              uuid PRIMARY KEY,
    qa_id           uuid,            -- 產生此研報的問答輪次（research.qa_log.id）
    conversation_id uuid,            -- 所屬對話串（對齊 qa_log 的 COALESCE 分組鍵）
    question        text NOT NULL,
    title           text,
    markdown        text NOT NULL,   -- 研報原始 markdown（真相來源）
    pdf_path        text,            -- 已渲染 PDF 檔位置（遺失時由 markdown 重建）
    sources         jsonb,           -- 當時引用來源（含編號，供卡片重現）
    thinking_ms     int,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_report_doc_qa
    ON research.report_doc (qa_id);
CREATE INDEX IF NOT EXISTS idx_report_doc_conversation
    ON research.report_doc (conversation_id, created_at);

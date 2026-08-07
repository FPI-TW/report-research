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
-- 顯示標題三欄（scripts/generate_titles.py 產出，讀取時零 LLM）。檔名多為券商流水號
-- （624726992507895929_260728_gs_umt.pdf），對讀者無意義，故改顯示報告內部標題。
-- title 一律繁體中文；NULL＝尚未產生，前端一律回退 file_name（不可靠此欄存在）。
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS title          text;
-- 原文標題（英文報告的原標題；中文報告與自擬標題為 NULL）
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS title_original text;
-- 來源：extracted（內文既有中文標題）／translated（英文標題譯為中文）／
-- generated（內文找不到標題，依重點自擬）。無 CHECK：值由批次寫入，未知一律存 NULL。
ALTER TABLE research.research_report ADD COLUMN IF NOT EXISTS title_source   text;

-- is_research 收斂成 NOT NULL DEFAULT true。
--
-- 原本可 NULL，於是全 repo 出現三種語意不同的過濾寫法：`= true`（overview 分面，
-- 排除 NULL）、`IS NOT FALSE`（其餘 15 處，含 NULL）、以及**完全不過濾**（檢索主路，
-- 因為 ingest_all.py 只對 is_research 的報告寫 chunk，chunk 存在本身就是那個保證）。
-- 三者今天結果相同純屬巧合——2026-07-30 實測 14,674 列**全部**是 true，零 NULL 零
-- false。一旦哪天有 NULL 進來，檢索頁／總覽題／閱讀頁的母體就會靜靜地分岔。
--
-- 選 true 而非 false 當預設：NULL 的語意是「標註器沒說」，而 15 處既有寫法都把它
-- 當研報看（`extract_takeaways.py` 那句註解寫得最明白）。填 true 是把現行行為寫進
-- schema，不是改變它。
--
-- 回填放在 SET NOT NULL 之前，否則有 NULL 時整個 make schema 會停在這裡。兩句都
-- 冪等：WHERE IS NULL 第二次就零列，而對已 NOT NULL 的欄位再 SET NOT NULL 是 no-op
-- （CI 的 schema job 會把 schema.sql 套兩次驗這件事）。
UPDATE research.research_report SET is_research = true WHERE is_research IS NULL;
ALTER TABLE research.research_report ALTER COLUMN is_research SET DEFAULT true;
ALTER TABLE research.research_report ALTER COLUMN is_research SET NOT NULL;

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

-- research_report 常用的過濾／排序欄位原本一個索引都沒有（只有 market 與三個陣列 GIN）。
-- 嚴重度要如實看待：現況約 1.4 萬列，且 full_text 多半 TOAST 出去、heap 本身不大，
-- 單次 seq scan 的量級估算只有數十毫秒（未實測）——這幾個索引是「規模一放大就線性惡化」
-- 的便宜保險，**不是已量測到的加速**。
-- 生產請先手動 `CREATE INDEX CONCURRENTLY`（同名即冪等，之後 make schema 的
-- IF NOT EXISTS 會直接跳過）；直接跑本檔會在建索引期間鎖住該表的寫入。
CREATE INDEX IF NOT EXISTS idx_rr_report_date
    ON research.research_report (report_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_rr_source
    ON research.research_report (source);
CREATE INDEX IF NOT EXISTS idx_rr_report_type
    ON research.research_report (report_type);
-- stock_code 是純量欄位，與 stock_targets 的 GIN 互不覆蓋：_COVERAGE_SQL 的
-- instrument_name 子查詢與 overview 個股條件的 OR 左支用的都是這一欄。
CREATE INDEX IF NOT EXISTS idx_rr_stock_code
    ON research.research_report (stock_code);
-- company_name 的唯一查法是 ILIKE '%名%'（overview 的 stock_name 條件），B-tree 用不上，
-- 只有 trgm GIN 有機會。pg_trgm 於上方 CREATE EXTENSION，故此索引必須排在它之後。
CREATE INDEX IF NOT EXISTS idx_rr_company_name_trgm
    ON research.research_report USING gin (company_name gin_trgm_ops);

-- 問答記錄（Phase 1 RAG）：每次 /api/ask 寫一列，供稽核/分析（冪等建表）
CREATE TABLE IF NOT EXISTS research.qa_log (
    id               uuid PRIMARY KEY,
    question         text NOT NULL,
    answer           text,
    cited_report_ids uuid[],                        -- 回答實際引用的報告 id
    filters          jsonb,                         -- 提問時套用的市場/商品/類型等篩選，
                                                    -- 外加路由遙測 path（落到哪一類）與
                                                    -- decided_by（precheck/overview/llm/fail_open）
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
-- M4b：證據帳本 manifest（{"schema_version":1,"evidence":[...]}；NULL＝舊列，空帳本語義）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS evidence_manifest jsonb;
-- M8：忠實度查核結果（citation_coverage/numeric_support_rate/faithfulness_score/claims；
-- 「來源支持度／待複核」非真實性保證；NULL＝未查核或 degraded fail-open）
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS evaluation jsonb;
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
-- M4b：證據帳本 manifest（與 qa_log 同格式；NULL＝舊列，空帳本語義）
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS evidence_manifest jsonb;

-- ── 觀點雷達訊號層：一列＝「一份研報 × 一個標的」的不可覆寫歷史快照 ──
-- 報告可涵蓋多個 stock_targets，故每個標的各一列。（研報觀點變化雷達設計規格「資料模型」）
-- 由 scripts/extract_signals.py 以 LLM 依固定 schema 擷取、Python 正規化後 upsert。
-- 讀取雷達時不呼叫 LLM，所有差異由 app/services/radar/ 決定性計算。
CREATE TABLE IF NOT EXISTS research.report_signal (
    id                    uuid PRIMARY KEY,
    -- 同 file_hash 重新 ingest 時（store.upsert_report 先刪後插）舊訊號連帶 CASCADE 清除，
    -- 批次下次偵測缺列自動補擷取＝要的冪等行為。
    report_id             uuid NOT NULL
                            REFERENCES research.research_report(id) ON DELETE CASCADE,
    market                text NOT NULL,               -- findb 市場代碼（對齊 research_report.market）
    instrument_code       text NOT NULL,               -- 個股代碼（來自 stock_targets）
    broker                text,                         -- 券商（來自 research_report.source，可 NULL）
    report_date           date,                         -- 報告日（來自 research_report.report_date）
    created_at            timestamptz NOT NULL DEFAULT now(),

    -- 評等：原文保留 + 正規化五級；無法映射 → 'unknown'（不計入分布，見設計規格）
    rating_raw            text,
    rating_normalized     text NOT NULL DEFAULT 'unknown'
        CHECK (rating_normalized IN
               ('buy','overweight','neutral','underweight','sell','unknown')),

    -- 目標價：保幣別、不換算、不混入跨券商中位數。numeric 不可用 float（精確計算）
    target_price          numeric(18,4),
    target_currency       text,
    target_horizon        text,
    target_price_evidence text,

    -- EPS：陣列，每筆 {fiscal_year, period, currency, unit, value, evidence}
    eps_estimates         jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- 四維論點：{outlook, catalyst, risk, valuation}，各 {stance, summary, evidence}
    thesis_dimensions     jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- 可追溯：schema/prompt 版本、擷取狀態、原始輸出、錯誤
    extraction_version    text NOT NULL,
    extraction_status     text NOT NULL DEFAULT 'pending'
        CHECK (extraction_status IN ('pending','valid','partial','rejected')),
    raw_payload           jsonb,
    error_detail          text,

    -- 一份研報對一個標的至多一列（不同報告＝不同快照各一列；同報告重擷取只更新該列）
    CONSTRAINT uq_report_signal_report_instr
        UNIQUE (report_id, market, instrument_code)
);

-- 索引（設計規格「建立索引」段）：總覽與券商時間線查詢
CREATE INDEX IF NOT EXISTS idx_report_signal_instr_date
    ON research.report_signal (market, instrument_code, report_date DESC);
-- 待查（勿逕自刪）：券商過濾一律走 radar/types.py 的 EFFECTIVE_BROKER_SQL
-- （COALESCE(r.source, s.broker) 跨表運算式），所以第三欄 broker 永遠不會被當過濾鍵用。
-- 但「從未被使用」是執行期斷言：planner 仍可能為只用 (market, instrument_code) 的查詢
-- 挑中它。要刪之前先查生產的 pg_stat_user_indexes.idx_scan（查法見 P1-11 的 manual_ddl）。
CREATE INDEX IF NOT EXISTS idx_report_signal_instr_broker_date
    ON research.report_signal (market, instrument_code, broker, report_date DESC);
-- idx_report_signal_report (report_id) 已刪：被 uq_report_signal_report_instr
-- (report_id, market, instrument_code) 的最左前綴完整覆蓋。勿再新增。
-- 批次 checkpoint / rerun：快速撈 pending/rejected/partial
CREATE INDEX IF NOT EXISTS idx_report_signal_status
    ON research.report_signal (extraction_status);

-- ── M7：研報生成重構（大綱 → 逐節）────────────────────────────────────────
-- 生成狀態機：一列＝一次生成請求的完整生命週期。冪等 request_key（同鍵重送回同
-- run）、每次狀態轉換原子 commit、checkpoint 供 fail-open 從最後一致點續跑。
-- id 由 Python uuid4 產生（比照 qa_log/report_doc/report_signal；無 DB DEFAULT）。
-- 對 research_report 刻意無 FK（生成流程史非語料衍生，upsert 先刪後插不應連帶清除）。
CREATE TABLE IF NOT EXISTS research.report_run (
    id                     uuid PRIMARY KEY,
    request_key            text NOT NULL,           -- 冪等鍵：端上合成 hash(question|filters|model|conversation_id)
    status                 text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','retrieving','outlining','drafting',
                          'verifying','rendering','completed','failed','cancelled')),
    input_config           jsonb NOT NULL DEFAULT '{}'::jsonb,  -- question/filters/model/prompt/renderer 快照
    evidence_manifest_hash text,
    outline                jsonb,                   -- 固定五章 + 動態子節
    checkpoint             jsonb,                   -- 最後一致可續跑點：{outline, final_positions[], current_revision_id}
    error_detail           text,
    current_revision_id    uuid,
    revision               int  NOT NULL DEFAULT 0,
    qa_id                  uuid,
    conversation_id        uuid,
    report_doc_id          uuid,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_report_run_request_key UNIQUE (request_key)
);
CREATE INDEX IF NOT EXISTS idx_report_run_status
    ON research.report_run (status);
CREATE INDEX IF NOT EXISTS idx_report_run_conversation
    ON research.report_run (conversation_id, created_at);

-- 逐節內容層：一列＝大綱一節。run_id CASCADE（緊耦合擁有，比照 report_chunk/
-- report_signal）；position 決定組裝序與 [n] 首見序；evidence_ids 對齊
-- evidence.py 的 evidence_id（16 hex）；draft/final 分離（draft=可覆寫 SSE 草稿）。
CREATE TABLE IF NOT EXISTS research.report_section (
    id             uuid PRIMARY KEY,
    run_id         uuid NOT NULL REFERENCES research.report_run(id) ON DELETE CASCADE,
    position       int  NOT NULL,                   -- 組裝序 = [n] 首見序
    section_key    text,                            -- 骨架節鍵（exec_summary/...）或動態子節鍵
    heading        text,
    draft_markdown text,                            -- 對應 SSE section_draft（可覆寫）
    final_markdown text,                            -- 對應 document_revision 組裝
    evidence_ids   text[],                          -- 被分配的 evidence_id 子集
    status         text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','retrieving','drafting','drafted','verifying','final','failed')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_report_section_run_pos UNIQUE (run_id, position)
);
-- idx_report_section_run (run_id, position) 已刪：與 uq_report_section_run_pos
-- UNIQUE (run_id, position) 逐欄（含欄序）完全相同。勿再新增。
CREATE INDEX IF NOT EXISTS idx_report_section_evidence
    ON research.report_section USING gin (evidence_ids);

-- report_doc 補欄（nullable 無 default，歷史列 NULL 安全退化）。
-- evidence_manifest 已由 M4b 建立，此處不重複定義。
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS outline             jsonb;
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS claim_evidence      jsonb;   -- claim/KPI/chart → evidence_id 映射
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS current_revision_id uuid;
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS report_run_id       uuid;    -- 反向連結（plain uuid，非 FK）
-- M8：忠實度查核結果（同 qa_log.evaluation 形狀；含 citation_coverage/numeric_support_rate/
-- faithfulness_score/claims；分數是來源支持度非真實性保證；NULL＝未查核或 degraded）
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS evaluation          jsonb;
-- M9b：目前渲染版本指標（指向 report_rendition；NULL＝尚無 rendition，下載回退 pdf_path）
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS current_rendition_id uuid;

-- 產出時的輸出語言與渲染模板（M10c 收尾）。
-- 沒有這兩欄時，locale/template_id 只活在「當初那個請求」裡：換皮重出（rerender）
-- 與 PDF 重建都會退回預設值 → 英文研報變成「英文內文 + 中文封面/頁首/免責 + 預設版型」。
-- 免責聲明是可轉寄 PDF 上最不該漂移的東西，而 M10c 才剛把它收斂到單一來源。
-- 歷史列為 NULL：讀取端一律 fail-open 到 zh-Hant / 預設模板（＝這些列產出時的實際值）。
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS locale text;
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS template_id text;

-- ── M9b 渲染產物層：不可變 rendition（換皮重出的歷史；同內容不同模板各一列）──
-- 換模板重出＝用既有 markdown 以另一模板產新 rendition，成功後原子切換
-- report_doc.current_rendition_id；不覆蓋歷史 PDF（每列 pdf_path 各異）。零 LLM。
CREATE TABLE IF NOT EXISTS research.report_rendition (
    id           uuid PRIMARY KEY,
    report_id    uuid NOT NULL,        -- 反向連結 report_doc（plain uuid，非 FK，比照既有慣例）
    renderer     text NOT NULL,        -- typst | weasyprint
    template_id  text,                 -- 選用模板（weasyprint 或未指定為 NULL）
    content_hash text NOT NULL,        -- markdown 的 sha256（換皮不重生內容 → 同 hash）
    pdf_path     text NOT NULL,
    status       text NOT NULL DEFAULT 'ready',
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_report_rendition_report
    ON research.report_rendition (report_id, created_at DESC);

-- ── 研報重點摘錄層：一列＝「一份研報 × 一條重點」（研報閱讀頁 /app/report/:hash）──
-- 由 scripts/extract_takeaways.py 以 LLM 回「論點 + 逐字引文」、Python 用
-- app/services/reading/anchor.py 確定性定位後寫入。讀取閱讀頁時不呼叫 LLM。
--
-- **quote_start/quote_end 錨定於 clean_extracted(full_text)，不是 full_text 本身。**
-- full_text 存的是未清理的原始抽取文字（見 scripts/ingest_all.py：full_text=raw_text
-- 但 chunks=chunk_text(clean_extracted(raw_text))），保留 CJK 間空白「台 積 電」。
-- 錨點基準字串／餵 LLM 的 excerpt／API 回傳的文字三者必須同一個 —— text_sha256
-- 就是為了讓這件事一旦被破壞會被偵測到（收回錨點，而非給出錯位的座標）。
-- 前端引文跳轉已於 2026-08-03 移除，這三欄目前無讀取路徑；批次仍照寫，
-- 因為 db_audit 的同源稽核靠它，且事後補算＝對 674+ 篇重跑 Sonnet。
CREATE TABLE IF NOT EXISTS research.report_takeaway (
    id                 uuid PRIMARY KEY,
    -- 同 file_hash 重新 ingest 時（store.upsert_report 先刪後插）連帶 CASCADE 清除，
    -- 批次下次偵測缺列自動補擷取＝要的冪等行為。
    report_id          uuid NOT NULL
                         REFERENCES research.research_report(id) ON DELETE CASCADE,
    ordinal            int  NOT NULL,           -- 1..N 顯示順序
    claim              text NOT NULL,           -- 論點（LLM）
    quote              text,                    -- 逐字引文（LLM，須出自正典文字）
    quote_start        int,                     -- 確定性錨定結果；NULL＝錨不到，條目仍照常顯示
    quote_end          int,
    anchor_method      text
        CHECK (anchor_method IS NULL
               OR anchor_method IN ('exact','normalized','prefix')),

    -- sha256(clean_extracted(full_text))：讀取時驗章，防 offset 漂移
    text_sha256        text NOT NULL,
    extraction_version text NOT NULL,
    extraction_status  text NOT NULL DEFAULT 'pending'
        CHECK (extraction_status IN ('pending','valid','partial','rejected')),
    raw_payload        jsonb,
    error_detail       text,
    created_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_report_takeaway_ordinal UNIQUE (report_id, ordinal),
    CONSTRAINT ck_report_takeaway_span
        CHECK (quote_start IS NULL
               OR (quote_start >= 0 AND quote_end > quote_start))
);

-- 讀取（依報告取全部摘錄、已排序）由 uq_report_takeaway_ordinal
-- UNIQUE (report_id, ordinal) 支撐；原 idx_report_takeaway_report 逐欄（含欄序）與它
-- 完全相同，已刪。勿再新增。
-- 批次 checkpoint / rerun
CREATE INDEX IF NOT EXISTS idx_report_takeaway_status
    ON research.report_takeaway (extraction_status);

-- 閱讀頁以 file_hash 為網址鍵（report_id 於重新 ingest 時會換新，分享連結會失效）。
-- file_hash 已是 UNIQUE，此處不需額外索引。

-- ─────────────────────────────────────────────────────────────
-- 每日簡報（一天一列；讀取時零 LLM）
-- ─────────────────────────────────────────────────────────────
-- 與 report_takeaway / report_signal 同一種分工：LLM 只在批次時產出語意（一段
-- markdown 綜述），窗期界定、來源清單與計數全由 Python 決定性計算並落庫，服務層
-- 只做 SELECT。
CREATE TABLE IF NOT EXISTS research.report_brief (
    id            uuid PRIMARY KEY,
    brief_date    date NOT NULL,

    -- 窗期以**入庫時間**（research_report.created_at）界定，不是 report_date。
    -- report_date 是研報自己標的日期，NAS 匯入的常比入庫日早——實測近 10 天入庫的
    -- 90 篇有 79 篇（88%）report_date 超過一天前，拿它界定窗期會靜默漏掉近九成，
    -- 與「排程不可用 --since-days」是同一個坑。
    window_start  timestamptz NOT NULL,
    window_end    timestamptz NOT NULL,

    markdown      text NOT NULL,

    -- 來源研報由 Python 記錄，不從 markdown 反推：簡報要能點回原文，而模型漏列或
    -- 多列一篇都不會有任何錯誤訊息。刻意不設 FK（uuid[] 無法 FK，且與另三張衍生表
    -- 一致）——代價是語料重建後可能留下孤兒 id，讀取端須容忍查不到的 id。
    report_ids    uuid[] NOT NULL DEFAULT '{}',
    report_count  int NOT NULL DEFAULT 0,
    signal_count  int NOT NULL DEFAULT 0,

    model         text,
    created_at    timestamptz NOT NULL DEFAULT now(),

    -- 一天一列。批次的冪等性完全靠這條：generate_brief.py 以「今天已有列」判斷跳過。
    CONSTRAINT uq_report_brief_date UNIQUE (brief_date)
);

-- 讀取只有兩種：最新一份（ORDER BY brief_date DESC LIMIT 1）與指定日期，
-- 兩者都由 uq_report_brief_date 的 UNIQUE 索引支撐。刻意不另建索引。

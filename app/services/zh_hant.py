"""簡體→繁體（台灣標準字形）正規化：只給 LLM 產出的顯示文字用。

四支批次的 prompt 都寫了「一律輸出繁體中文」，模型仍會偶發輸出整句簡體——
2026-07-31 的台股頭條就是一例（`title_source=translated`，模型把英文標題翻成了
簡體）。prompt 是機率性保證，本模組是確定性的後處理，比照本 repo 的一貫作法
（用 Python 收尾，不去改平行的 prompt 副本）。

三個刻意的設計，每一個都是實測出來的：

1. **判別用「Big5 可編碼性」，不用 opencc 的轉換結果。**
   opencc 對「本來就是繁體」的文字照樣會動手：台→臺、干→幹、占→佔、周→週、
   布→佈、里→裏。全語料實測拿 s2tw 直接掃，10,326 篇摘要有 7,944 篇「命中」，
   而其中「船期干擾」被改成「船期幹擾」——那是把正確的字改錯。這些字本身就是
   繁體字，只是同時被簡化字表借去當某個繁體字的簡化形。真正的判準是「這個字
   是不是繁體字」，而 Big5 就是台灣的繁體字集，可編碼性是零依賴的確定性檢驗。

2. **門檻是「至少 2 個字」且「密度至少 5%」，兩條缺一不可。**
   *字數*那條擋專有名詞：全語料 25 則命中裡，只含 1 個簡體字的 12 則全是券商
   自己的寫法——恒耀（8349）在 49 篇原文都寫「恒」、0 篇寫「恆」；新美齊的建案
   「画世代」4 篇原文全寫「画」。改掉它們是錯的。真正的簡體文字最少也有 3 個
   （密度 8.6% 起跳），1 與 3 之間有乾淨的空隙。
   *密度*那條擋長文件：字數門檻是拿標題／摘錄（25-35 個漢字）校準的，**放到
   幾千字的長篇 markdown 上就形同虛設**——一份純繁體長文只要提到「恒耀」兩次
   就會被整份轉換。密度把這種情形壓到 0.04%，而真正的簡體文件是 20-50%。
   5% 這個值在實測上兩邊都有餘裕：最低的真陽性 8.6%、最高的假陽性 7.7%（而後者
   本來就先被字數擋掉了）。
   代價是已知的：「江苏現貨」這種單字漏網不會被修（那是真的該修），寧可漏修
   也不要改壞專有名詞。反過來，達門檻的字串是**整串**轉的，所以半繁半簡的句子
   裡，本來就正確的那半邊也會跟著過一次詞組表（「公布」→「公佈」、「布局」→
   「佈局」）。那是同一個取捨的另一面：兩者都是合法的繁體用字，而簡體字不是。

3. **轉換用 s2tw，不是 s2t，也不是 s2twp。**
   s2t 是 OpenCC 標準字：群联→羣聯、为→爲、里→裏，全都不是台灣用字（群聯
   8299 就叫群聯）。s2twp 則會連詞彙一起換（数据→資料），那已經超出「字形」
   的範圍、會動到專有名詞。只有 s2tw 剛好是純字形的台灣標準。
   轉完再把「臺」收斂回「台」：語料標題含「台」1,068 篇、含「臺」2 篇，
   一則「臺積電」夾在一整排「台積電」裡只會像壞掉。（檯燈／颱風走的是
   opencc 的詞組表，出來就不是「臺」，不受這步影響。）

**查表鍵另走 `lookup_key`**：訊號擷取的評等／幣別詞只有 2-4 個漢字（「买入」只含 1 個簡體字），
上面的門檻會讓它們永遠轉不動。查表鍵不存檔也不顯示，所以不套門檻；存下來的仍是原值。

**不適用的地方**（各有各的理由，別順手接上去）：
- `report_takeaway.quote`：**逐字引文**。第一個理由與任何功能無關——改一個字它就不再是
  逐字引文，而「原文就是這麼寫的」正是它存在的全部意義（全語料 63 篇原文本身就是簡體）。
  第二個理由是它仍是 `reading/anchor.locate_quote` 的錨定基準，批次照樣在寫
  quote_start/quote_end；轉了會靜默錨不回 canonical text。
  **第三個理由是 2026-08-04 新增的、而且使用者看得見**：quote 現在會被原樣拿去當
  PDFium 的搜尋關鍵字（閱讀頁「在原文中尋找」，見 frontend/.../pdf/quoteNeedle.ts）。
  轉了繁體就搜不到原本是簡體的那 63 篇，讀者會看到「原文中找不到這段文字」。
  也就是說這條規則的破壞從「靜默錨不回去」升級成「直接壞在畫面上」。
- `report_signal.thesis_dimensions[*].evidence`：同理，是原句。
- `research_report.full_text` / `report_chunk.content`：語料本身（全語料 63 篇
  原文就是簡體）。不是 LLM 產出，動它等於竄改來源；chunk 更是碰不得
  （見 CLAUDE.md 對 `make normalize` 死法的記載）。
"""

from __future__ import annotations

from functools import lru_cache

# 少於這個數量的簡體字視為專有名詞（券商自己的寫法），整串不動。理由見模組 docstring。
MIN_SIMPLIFIED_CHARS = 2

# 簡體字占漢字的比例下限。字數門檻是拿標題／摘錄（25-35 漢字）校準的，長文件必須
# 另外靠密度把關，否則一份幾千字的純繁體研報提到兩次「恒耀」就會被整份轉換。
MIN_SIMPLIFIED_RATIO = 0.05


@lru_cache(maxsize=1)
def _converter():
    """OpenCC s2tw 轉換器。延後建構：載入詞典要數 MB，而 web 完全不需要它
    （只有批次會寫這些欄位），不該讓它進到 web 的啟動成本裡。
    """
    from opencc import OpenCC

    return OpenCC("s2tw")


@lru_cache(maxsize=8192)
def is_simplified_only(ch: str) -> bool:
    """這個字是否「只存在於簡體」＝不是繁體字、且有對應的繁體形。

    兩個條件缺一不可：Big5 編不出來（不是繁體字）**且** opencc 轉得動它
    （確實是某個繁體字的簡化形，而不是日文漢字或 Big5 收不到的罕用繁體字，
    例如「喆」「堃」）。
    """
    try:
        ch.encode("big5")
    except UnicodeEncodeError:
        return _converter().convert(ch) != ch
    return False


def count_simplified(text: str) -> int:
    """字串裡「只存在於簡體」的字數（重複計數）。"""
    if not text:
        return 0
    return sum(1 for ch in text if is_simplified_only(ch))


def count_han(text: str) -> int:
    """漢字字數（CJK 統一表意文字），密度的分母。

    刻意只數漢字：答案與研報摻雜大量英文、數字與 markdown 記號，拿總長度當分母
    會把密度稀釋到門檻以下，長文件就永遠轉不動。
    """
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def looks_simplified(text: str) -> bool:
    """整串是否該視為簡體中文：字數與密度**兩條門檻都要過**。

    只看字數，長文件會誤判（純繁體研報提到兩次專有名詞就中）；只看密度，短字串
    會誤判（13 個漢字裡一個「恒」就是 7.7%）。兩條各擋一種，理由見模組 docstring。
    """
    n = count_simplified(text)
    if n < MIN_SIMPLIFIED_CHARS:
        return False
    han = count_han(text)
    return han > 0 and n / han >= MIN_SIMPLIFIED_RATIO


def to_traditional(text: str) -> str:
    """達門檻才整串轉繁體（台灣標準字形）；否則原樣回傳。

    整串轉而非逐字轉，是為了讓 opencc 的詞組表去解一對多的字：簡體的「余／后／
    里／干／面」對應到哪個繁體字要看詞（盈余→盈餘、后天→後天），逐字轉會挑錯。
    整串轉之所以安全，正是因為門檻已經先判定「這整串是簡體文字」。
    """
    if not text or not looks_simplified(text):
        return text
    return _converter().convert(text).replace("臺", "台")


def lookup_key(text: str) -> str:
    """查表用的鍵：含任一「只存在於簡體」的字就整串轉繁體；否則原樣回傳。

    **只給查表用，結果不得存檔或顯示**（例如訊號擷取的評等／幣別：`signal_extract.normalize_rating`
    拿它對 `RATING_MAP`，存進 DB 的 `rating_raw` 仍是原值）。與 `to_traditional` 刻意不同：

    - **不套字數與密度門檻**。那兩條是為了不改壞「要存下來給人看」的專有名詞；評等詞只有 2-4 個
      漢字，「买入」「卖出」「减持」各只含 1 個簡體字，套門檻就永遠轉不動——而這正是要對上的情形。
      查表鍵不會被存下來，改壞專有名詞的風險不存在。
    - **沒有簡體字就完全不動**：純繁體原文不過 opencc（台→臺、占→佔那一類改動），既有資料的查表
      結果逐字不變。

    **限制**：「簡體字」沿用 `is_simplified_only` 的判別（Big5 編不出來），所以 Big5 也收錄、
    s2tw 仍會轉的 136 個簡化形（例如「优 于 后 几 荐 台 干」）不算簡體——整串只由這類字組成時
    原樣回傳，「优于大市」「推荐」仍查不到。有其他簡體字時整串會轉（「强烈推荐」→「強烈推薦」），
    但轉出來的詞表裡未必有。補大陸評等詞（「推荐」「强烈推荐」「谨慎推荐」「跑赢行业」等）是
    `RATING_MAP` 的詞表工作，另開 PR，不靠放寬這裡的判別。
    """
    if not text or count_simplified(text) == 0:
        return text
    return _converter().convert(text).replace("臺", "台")

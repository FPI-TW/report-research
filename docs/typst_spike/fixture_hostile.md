# 敵意輸入測試：Typst 語法注入與跳脫

## 注入嘗試（必須以字面文字呈現，不得被執行）

#eval("1+1") 與 #read("/etc/passwd") 與 #import "@preview/evil:1.0.0"

行首井號測試：
#set text(size: 30pt)

數學模式：$ x^2 + y^2 = z^2 $ 與行內 $100 美元、EPS $14.2 元。

參照語法：@citation 與 <label-target> 與 #footnote[惡意]。

## 特殊字元

星號 *不是粗體* 字面星號2*3=6、底線 _not italic_ snake_case_name、方括號 [這不是連結]、[1][2] 引用編號。

反斜線 C:\Users\test\file.txt、百分比 57.8%、井字標籤 #hashtag、And 符號 R&D 部門、波浪 ~約略值、錢號區間 $880–$1,088。

雙斜線註解測試 // 這不是註解 且 /* 這也不是 */。

<div class="html">HTML 片段</div> 與 <br/> 與 &amp; 實體。

反引號行內碼：`#eval("code")` 與 `SELECT * FROM users;`。

連字號序列 --- 與 -- 以及省略號 ...

## 邊界結構

- 巢狀清單第一層
  - 第二層含 #context 關鍵字
    1. 第三層數字清單 $var

| 含管線的欄位 | 特殊字元欄 |
|--------------|------------|
| a \| b | #union 與 $type |
| `code#1` | *star* _under_ |

> 引言中包含 #quote 指令與 $math$ 符號

空連結測試 [](https://example.com) 與裸網址 https://example.com/path?q=1&r=2%20test

```python
# 程式碼區塊中的井號註解
print("#not-typst")
```

結尾未閉合星號 *dangling 與未閉合反引號 `dangling

## #eval("HEADING-INJECTION-RAN")

標題向量：章節標題是唯一未經 pandoc 跳脫的 LLM 原文（章節刻意在 pandoc 之前切），
emitter 必須以字串常值輸出，否則此標題會被求值。

## #read("/.env")

標題向量：未以字串常值輸出時，repo root 的 .env（含共用帳密與 DB 連線字串）會被整份
渲染進一份可下載、可轉發的 PDF。root 限制擋得住 ../ 逃逸，擋不住 repo 樹內的檔案。

## 標題] #text(fill: red)[HIJACKED] #hide[

標題向量：方括號可脫出 content block 再接任意指令。

## 2026 年 EPS 上修 $14.2 至 $16.8

完全正常的財經標題。D6 的 gfm-tex_math_dollars 只作用於 pandoc，對標題無效——未以
字串常值輸出時，兩個錢號會被配對成數學模式吃掉內容（編譯成功、零錯誤，但字沒了）。

## 依 @法說會 資料

標題向量：未以字串常值輸出時編譯失敗（label 不存在）→ 整份研報無聲退回 WeasyPrint。

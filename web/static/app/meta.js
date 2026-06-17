/*
 * 廷豐研報 前端模組：詞表常數 + label/color 純查表（無 DOM、無 state，相依樹的葉節點）。
 */

// 市場標籤對齊 findb 代碼；顯示中文名 + 配色
export const MARKET_META = {
  TW:{label:"台股",color:"#34c759"}, US:{label:"美股",color:"#007aff"},
  HK:{label:"港股",color:"#ff9500"}, CN:{label:"陸股",color:"#ff3b30"},
  FX:{label:"外匯",color:"#00c7be"}, WTX:{label:"台指期",color:"#af52de"},
  MACRO:{label:"總經",color:"#ff2d55"}, GLOBAL:{label:"全球",color:"#5856d6"},
  CRYPTO:{label:"加密",color:"#a2845e"}
};
export const mLabel = c => (MARKET_META[c] && MARKET_META[c].label) || c || "—";
export const mColor = c => (MARKET_META[c] && MARKET_META[c].color) || "#8e8e93";

// 商品類型詞表：中文 label + 配色（對齊 tagging.py INSTRUMENT_DISPLAY）
export const INSTRUMENT_META = {
  equity:{label:"股票",color:"#0a84ff"}, index:{label:"指數",color:"#5e5ce6"},
  futures:{label:"期貨",color:"#ff9f0a"}, options:{label:"選擇權",color:"#bf5af2"},
  etf:{label:"ETF",color:"#30d158"}, bond:{label:"債券",color:"#0bb8c4"},
  fx:{label:"外匯",color:"#00c7be"}, commodity:{label:"原物料",color:"#ac8e68"},
  crypto:{label:"加密",color:"#e0a400"}
};
export const iLabel = c => (INSTRUMENT_META[c] && INSTRUMENT_META[c].label) || c || "—";
export const iColor = c => (INSTRUMENT_META[c] && INSTRUMENT_META[c].color) || "#8e8e93";

// 報告類型詞表多為中文（雙週報/週報/月報/速報/策略/評析/報告）；英文者補中文顯示
export const REPORT_TYPE_LABEL = { snapshot: "快照", memo: "備忘" };
export const tLabel = t => REPORT_TYPE_LABEL[t] || t || "—";

export function fmtDate(d) { return d ? d.replace(/-/g, "/") : null; }

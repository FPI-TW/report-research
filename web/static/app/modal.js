/*
 * 廷豐研報 前端模組：完整報告 modal（PDF 內嵌預覽 + 焦點陷阱）。
 */
import { $ } from "/static/app/dom.js";
import { html, fetchJSON } from "/static/utils.js";
import { mColor, mLabel, fmtDate } from "/static/app/meta.js";

let lastFocused = null;
function trapTab(e) {
  if (e.key !== "Tab") return;
  const modal = document.querySelector(".modal");
  const f = modal.querySelectorAll('a[href],button:not([disabled]),iframe,[tabindex]:not([tabindex="-1"])');
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}
export async function openFull(id) {
  const back = $("#modalBackdrop");
  $("#modalName").textContent = "載入中…";
  $("#modalMeta").innerHTML = "";
  const msum = $("#modalSummary");
  msum.textContent = ""; msum.hidden = true;
  $("#modalBody").textContent = "";
  $("#modalFoot").innerHTML = "";
  lastFocused = document.activeElement;       // 記住觸發按鈕，關閉時還原焦點
  back.classList.add("open");
  document.body.style.overflow = "hidden";   // 鎖背景捲動
  $("#modalClose").focus();
  document.addEventListener("keydown", trapTab);
  try {
    const d = await fetchJSON(`/api/report/${id}/full`);
    const meta = [];
    if (d.market) meta.push(html`<span class="badge" style="background:${mColor(d.market)}">${mLabel(d.market)}</span>`);
    if (d.source) meta.push(html`<span>${d.source}</span>`);
    if (d.report_date) meta.push(html`<span>${fmtDate(d.report_date)}</span>`);
    if (d.report_type) meta.push(html`<span>${d.report_type}</span>`);
    $("#modalName").textContent = d.file_name || "";
    $("#modalMeta").innerHTML = meta.join('<span class="dot">·</span>');
    if (d.summary) { msum.textContent = d.summary; msum.hidden = false; }
    const fileUrl = `/api/report/${id}/file`;
    const isPdf = (d.file_name || "").toLowerCase().endsWith(".pdf");
    if (d.has_file && isPdf) {
      // PDF 常數 MB，內網下要數秒；先顯示載入中覆蓋層，iframe load 後移除，避免整片深灰無回饋
      $("#modalBody").innerHTML = html`<div class="pdf-wrap">
          <div class="pdf-loading" id="pdfLoading"><span class="spin"></span>報告載入中…</div>
          <iframe class="pdf-frame" src="${fileUrl}" title="完整報告"></iframe>
        </div>`;
      const frame = $("#modalBody iframe"), ld = $("#pdfLoading");
      frame.onload = () => { if (ld) ld.remove(); };
      frame.onerror = () => { if (ld) ld.textContent = "無法預覽，請改用下方「在新分頁開啟」"; };
      $("#modalFoot").innerHTML = html`<a href="${fileUrl}" target="_blank" rel="noopener">在新分頁開啟</a>`;
    } else if (d.has_file) {
      const ext = (d.file_name.split(".").pop() || "").toUpperCase();
      $("#modalBody").innerHTML = html`<div class="modal-fallback">此檔為 ${ext} 文件，無法內嵌預覽，請下載查看。</div>`;
      $("#modalFoot").innerHTML = html`<a href="${fileUrl}" target="_blank" rel="noopener">下載原始檔</a>`;
    } else {
      $("#modalBody").innerHTML = `<div class="modal-fallback">找不到原始檔。</div>`;
      $("#modalFoot").innerHTML = "";
    }
  } catch (e) {
    $("#modalName").textContent = "載入失敗";
    $("#modalBody").innerHTML = `<div class="modal-fallback">報告載入失敗，請稍後再試或重新整理頁面。</div>`;
  }
}
export function closeFull() {
  document.removeEventListener("keydown", trapTab);
  $("#modalBackdrop").classList.remove("open");
  $("#modalBody").innerHTML = "";
  document.body.style.overflow = "";          // 還原背景捲動
  if (lastFocused && lastFocused.focus) lastFocused.focus();
  lastFocused = null;
}

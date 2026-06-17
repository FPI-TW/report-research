/*
 * 廷豐研報 前端模組：DOM 查詢小工具（querySelector 版）。
 * 注意：monitor.html 自有 getElementById 版的 $，不可改用此處的 $。
 */
export const $ = s => document.querySelector(s);
export const $$ = s => document.querySelectorAll(s);

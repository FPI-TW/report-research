import adminStyles from '../Admin.module.css'

/** 尚未接上 API 的維運子頁：只說明「尚未提供」與目前可替代的看法，不發任何請求。 */
function Placeholder({ title, body }: { title: string; body: string }) {
  return (
    <section className={adminStyles.card} aria-labelledby="ops-placeholder-title">
      <h2 id="ops-placeholder-title" className={adminStyles.ctitle}>{title}（尚未提供）</h2>
      <p className={adminStyles.idle}>{body}</p>
    </section>
  )
}

export function OpsIncidentsPage() {
  return (
    <Placeholder
      title="事件"
      body="事件清單尚未提供。目前事件仍由主機上的事件偵測探針判定，並經告警 webhook 通知。"
    />
  )
}

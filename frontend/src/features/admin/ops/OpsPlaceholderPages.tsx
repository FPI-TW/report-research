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

export function OpsJobsPage() {
  return (
    <Placeholder
      title="排程工作"
      body="排程工作的執行紀錄與「立即執行」尚未提供。目前可在「服務」看各 timer 的上次與下次觸發時間，在「日誌」看每次執行的輸出。"
    />
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

export function OpsHostPage() {
  return <Placeholder title="主機" body="主機資源（CPU、記憶體、磁碟）的觀測尚未提供。" />
}

import type { AdminPageKey } from '../../components/shell/adminNav'
import type {
  DataHealthResponse, IncidentListResponse, OpsDependencyGraph, OpsRemoteHostList, OpsServiceListResponse,
  SecurityAlertsResponse,
} from '../../lib/generated/adminApi'
import type { Progress } from '../../lib/progressSchema'
import { componentLabel, fmtDuration, needsAttention } from './ops/opsLabels'
import { attentionLevel, pipelineAttention } from './ops/pipeline/attention'
import { REMOTE_STATE_LABELS } from './ops/remoteHostLabels'
import { STATE_LABELS } from './security/securityLabels'

/**
 * 導覽的燈號與數字。**只用各頁既有的判準，不另外發明門檻**：
 *
 *   服務      有服務失敗或找不到（核心層不在運作中也算，`opsLabels.needsAttention`）→ bad；
 *             維運代理連不上 → warn（狀態不明不等於正常）。排程服務兩次執行之間的「閒置」是正常。
 *   依賴圖    外部依賴（R2、DeepSeek、NAS…）的健康判讀由後端算好：down → bad、degraded → warn。
 *             沒有探針的外部依賴是 unknown（未監控），不亮。內部服務壞掉算在「服務」，這裡不重複計。
 *   來源主機  把新研報推上來的那台主機：後端判讀出原因（失聯、同步停擺、推送失敗…）→ bad。
 *             沒設定要監看、或判斷不出來（R2 讀不到）不亮——後者由物件儲存那一組告警。
 *   管線      `pipeline/attention.ts`：近 24 小時有排程失敗 → bad；忠實度查核 fail-open → warn。
 *   事件      有進行中的事件 → warn，數字是進行中的筆數。
 *   主機      有進行中的主機資源事件（元件 host）→ warn。主機頁本身沒有門檻，事件偵測才有。
 *   資料健康  後端的整體判讀：fail → bad、warn → warn（unknown 不亮）。
 *   安全      告警觸發中（`/api/admin/security/alerts` 的 state 不是 ok／unknown）→ bad。
 *
 * 待複核、待審草稿是常態非零的佇列：只給數字，不亮燈——算進去燈號會永遠亮著，兩週內就沒人看。
 * 資料還沒回來或讀取失敗的頁面不亮燈（代理連不上是例外，見上）。
 */
export type Lamp = 'bad' | 'warn'

export interface PageSignal {
  lamp?: Lamp
  /** 次級面板右側的數字（進行中的事件、待處理的筆數）。0 不顯示。 */
  count?: number
  /** 一句話的原因，總覽的「異常與注意」用。 */
  reason?: string
  detail?: string
}

export type AdminSignals = Partial<Record<AdminPageKey, PageSignal>>

export interface SignalInputs {
  services?: OpsServiceListResponse
  agentUnavailable?: boolean
  dependencies?: OpsDependencyGraph
  remoteHosts?: OpsRemoteHostList
  progress?: Progress
  firingIncidents?: IncidentListResponse
  dataHealth?: DataHealthResponse
  securityAlerts?: SecurityAlertsResponse
  /** 三個待複核分頁（忠實度低分、倒讚、抽取品質）各自的待處理數；沒回來的是 undefined。 */
  reviewOpen?: { faithfulness?: number; feedback?: number; extraction?: number }
  draftUploads?: number
}

export function deriveSignals(input: SignalInputs): AdminSignals {
  const out: AdminSignals = {}

  if (input.services) {
    const problems = input.services.items.filter(needsAttention)
    if (problems.length > 0) {
      out.services = {
        lamp: 'bad',
        reason: `${problems.length} 個服務需要處理`,
        detail: problems.map(s => s.name).join('、'),
      }
    }
  } else if (input.agentUnavailable) {
    out.services = { lamp: 'warn', reason: '維運代理連不上', detail: '服務狀態、依賴圖與日誌暫時讀不到' }
  }

  if (input.dependencies) {
    const ext = input.dependencies.nodes.filter(n => n.kind === 'external')
    const down = ext.filter(n => n.health === 'down')
    const degraded = ext.filter(n => n.health === 'degraded')
    if (down.length > 0) {
      out.dependencies = {
        lamp: 'bad',
        reason: `${down.length} 個外部依賴故障`,
        detail: down.map(n => (n.health_reason ? `${n.name}（${n.health_reason}）` : n.name)).join('、'),
      }
    } else if (degraded.length > 0) {
      out.dependencies = {
        lamp: 'warn',
        reason: `${degraded.length} 個外部依賴降級`,
        detail: degraded.map(n => (n.health_reason ? `${n.name}（${n.health_reason}）` : n.name)).join('、'),
      }
    }
  }

  const troubled = (input.remoteHosts?.items ?? []).filter(h => h.reasons.length > 0)
  if (troubled.length > 0) {
    out.remoteHosts = {
      lamp: 'bad',
      reason: `來源主機異常：${troubled.map(h => h.name).join('、')}`,
      detail: troubled.flatMap(h => h.reasons.map(r => REMOTE_STATE_LABELS[r])).join('、'),
    }
  }

  if (input.progress) {
    const items = pipelineAttention(input.progress)
    const level = attentionLevel(items)
    if (level !== 'ok') {
      const first = items.find(i => i.level === level) ?? items[0]
      out.pipeline = {
        lamp: level,
        reason: first.text,
        detail: items.length > 1 ? `另有 ${items.length - 1} 項` : undefined,
      }
    }
  }

  if (input.firingIncidents && input.firingIncidents.total > 0) {
    const { total, items } = input.firingIncidents
    const first = items[0]
    out.incidents = {
      lamp: 'warn',
      count: total,
      reason: `${total} 個事件進行中`,
      detail: first
        ? `${componentLabel(first.component)}・${first.summary || first.reason}・已 ${fmtDuration(elapsedSeconds(first.opened_at))}`
        : undefined,
    }
    const host = items.find(i => i.component === 'host')
    if (host) out.host = { lamp: 'warn', reason: '主機資源事件進行中', detail: host.summary || host.reason }
  }

  if (input.dataHealth && (input.dataHealth.overall === 'fail' || input.dataHealth.overall === 'warn')) {
    const d = input.dataHealth
    const parts = ([['批次新鮮度', d.freshness.status], ['資料稽核', d.db_audit.status], ['R2 對帳', d.r2_reconcile.status]] as const)
      .filter(([, status]) => status === 'fail' || status === 'warn')
      .map(([label]) => label)
    out.dataHealth = {
      lamp: d.overall === 'fail' ? 'bad' : 'warn',
      reason: d.overall === 'fail' ? '資料健康異常' : '資料健康需要注意',
      detail: parts.join('、') || undefined,
    }
  }

  const sec = input.securityAlerts
  if (sec && sec.state !== 'ok' && sec.state !== 'unknown') {
    const triggered = sec.triggered.filter(t => t !== 'ok' && t !== 'unknown')
    out.security = {
      lamp: 'bad',
      reason: alertLabel(sec.state),
      detail: triggered.length > 1 ? triggered.map(alertLabel).join('、') : undefined,
    }
  }

  const rv = input.reviewOpen
  if (rv) {
    const counts = [rv.faithfulness, rv.feedback, rv.extraction]
    if (counts.some(n => n != null)) {
      const total = counts.reduce<number>((sum, n) => sum + (n ?? 0), 0)
      out.reviews = {
        count: total,
        detail: `忠實度低分 ${rv.faithfulness ?? '—'}・倒讚 ${rv.feedback ?? '—'}・抽取品質 ${rv.extraction ?? '—'}`,
      }
    }
  }

  if (input.draftUploads != null) out.uploads = { count: input.draftUploads }

  return out
}

const alertLabel = (state: string): string => STATE_LABELS[state] ?? state

function elapsedSeconds(since: string): number | null {
  const t = Date.parse(since)
  return Number.isNaN(t) ? null : Math.max(0, (Date.now() - t) / 1000)
}

/** 一組頁面裡最嚴重的燈：有 bad 就是 bad，否則有 warn 就是 warn。圖示軌的紅點／橘點用。 */
export function worstLamp(signals: AdminSignals, pages: readonly AdminPageKey[]): Lamp | null {
  let worst: Lamp | null = null
  for (const key of pages) {
    const lamp = signals[key]?.lamp
    if (lamp === 'bad') return 'bad'
    if (lamp === 'warn') worst = 'warn'
  }
  return worst
}

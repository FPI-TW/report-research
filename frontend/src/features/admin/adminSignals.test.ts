import { describe, expect, it } from 'vitest'
import type {
  DataHealthResponse, IncidentListResponse, OpsDependencyGraph, OpsDependencyNode, OpsServiceListResponse, OpsServiceStatus,
  SecurityAlertsResponse,
} from '../../lib/generated/adminApi'
import { deriveSignals, worstLamp } from './adminSignals'
import { progressFixture } from './ops/pipeline/pipelineTestKit'

const svc = (name: string, tier: OpsServiceStatus['tier'], summary: OpsServiceStatus['summary']): OpsServiceStatus => ({
  name, kind: 'systemd', tier, target: `report-mark-${name}.service`, timer: null, actions: ['status'],
  description: '', summary, error: null, systemd: null, container: null, timer_state: null,
})
const services = (...items: OpsServiceStatus[]): OpsServiceListResponse =>
  ({ environment: 'production', host: 'h', checked_at: '2026-10-06T02:00:00Z', items })

const incidents = (...components: string[]): IncidentListResponse => ({
  since: 'a', until: 'b', total: components.length, limit: 50, offset: 0, has_more: false, next_offset: null,
  items: components.map((component, i) => ({
    incident_id: `i${i}`, host: 'h', component, kind: 'service' as const, status: 'firing' as const,
    severity: 'WARNING' as const, reason: 'r', summary: `${component} 出事`, opened_at: '2026-10-06T01:00:00Z',
    last_event_at: '2026-10-06T01:30:00Z', event_count: 1,
  })),
})

const section = (status: 'ok' | 'warn' | 'fail' | 'unknown') => ({ status, available: true, stale: false, skipped: [], findings: [] })
const dataHealth = (overall: DataHealthResponse['overall'], parts: Partial<Record<'f' | 'a' | 'r', 'ok' | 'warn' | 'fail'>> = {}) => ({
  generated_at: 'x', overall,
  freshness: { status: parts.f ?? 'ok', exit_code: 0, findings: [] },
  db_audit: section(parts.a ?? 'ok'),
  r2_reconcile: { ...section(parts.r ?? 'ok'), issues: [], issues_total: 0 },
}) as unknown as DataHealthResponse

const alerts = (state: SecurityAlertsResponse['state'], triggered: SecurityAlertsResponse['triggered'] = []): SecurityAlertsResponse => ({
  state, triggered, window_minutes: 15, login_failures: 0, login_failure_threshold: 20, accounts_over: [],
  max_account_failures: 0, account_failure_threshold: 5, elevate_failures: 0, elevate_failure_threshold: 5, audit_chain: 'ok',
})

const node = (name: string, kind: OpsDependencyNode['kind'], health: OpsDependencyNode['health'], reason = ''): OpsDependencyNode => ({
  name, kind, tier: 'important', health, health_reason: reason, depends_on: [], dependents: [], layer: 0, affected: false, impacted_by: [],
})
const graph = (...nodes: OpsDependencyNode[]): OpsDependencyGraph => ({
  environment: 'production', host: 'h', checked_at: '2026-10-06T02:00:00Z', nodes, edges: [], down: [], root_causes: [], affected: [],
})

describe('導覽燈號', () => {
  it('什麼都還沒讀到：不亮燈、沒有數字', () => {
    expect(deriveSignals({})).toEqual({})
  })

  it('全部正常：不亮燈', () => {
    const s = deriveSignals({
      services: services(svc('web', 'critical', 'running'), svc('sync', 'important', 'idle')),
      progress: progressFixture(), firingIncidents: incidents(), dataHealth: dataHealth('ok'), securityAlerts: alerts('ok'),
    })
    expect(Object.values(s).some(x => x?.lamp)).toBe(false)
  })

  it('服務：失敗或找不到 → bad；排程服務閒置是正常；核心層不在運作中也算', () => {
    expect(deriveSignals({ services: services(svc('backup', 'important', 'failed')) }).services)
      .toMatchObject({ lamp: 'bad', detail: 'backup' })
    expect(deriveSignals({ services: services(svc('x', 'supporting', 'not_found')) }).services?.lamp).toBe('bad')
    expect(deriveSignals({ services: services(svc('sync', 'important', 'idle')) }).services).toBeUndefined()
    expect(deriveSignals({ services: services(svc('nginx', 'critical', 'idle')) }).services?.lamp).toBe('bad')
    expect(deriveSignals({ services: services(svc('web', 'critical', 'transitioning')) }).services).toBeUndefined()
  })

  it('維運代理連不上：服務亮 warn（狀態不明不等於正常）', () => {
    expect(deriveSignals({ agentUnavailable: true }).services?.lamp).toBe('warn')
  })

  it('依賴圖：外部依賴故障 → bad、降級 → warn；未監控（unknown）與內部服務不算', () => {
    expect(deriveSignals({ dependencies: graph(node('r2', 'external', 'down', '探針逾時'), node('nas', 'external', 'degraded')) }).dependencies)
      .toMatchObject({ lamp: 'bad', reason: '1 個外部依賴故障', detail: 'r2（探針逾時）' })
    expect(deriveSignals({ dependencies: graph(node('deepseek', 'external', 'degraded')) }).dependencies)
      .toMatchObject({ lamp: 'warn', detail: 'deepseek' })
    expect(deriveSignals({ dependencies: graph(node('slack', 'external', 'unknown'), node('r2', 'external', 'ok')) }).dependencies).toBeUndefined()
    // 內部服務壞掉算在「服務」那一顆，依賴圖不重複亮
    expect(deriveSignals({ dependencies: graph(node('backup', 'systemd', 'down')) }).dependencies).toBeUndefined()
  })

  it('來源主機：後端判讀出原因 → bad；沒設定、正常、判斷不出來都不亮', () => {
    const host = (name: string, state: string, reasons: string[]) => ({ name, state, reasons }) as never
    expect(deriveSignals({ remoteHosts: { items: [host('office', 'push_failed', ['push_failed', 'round_aborted'])] } }).remoteHosts)
      .toMatchObject({ lamp: 'bad', reason: '來源主機異常：office', detail: '推送失敗、上一輪被中止' })
    expect(deriveSignals({ remoteHosts: { items: [host('office', 'ok', []), host('lab', 'unknown', [])] } }).remoteHosts).toBeUndefined()
    expect(deriveSignals({ remoteHosts: { items: [] } }).remoteHosts).toBeUndefined()
  })

  it('管線沿用 attention.ts：近 24 小時有排程失敗 → bad；只有 fail-open → warn', () => {
    const base = progressFixture()
    const failed = progressFixture({ unit_failures: { ...base.unit_failures!, count_24h: 2 } })
    expect(deriveSignals({ progress: failed }).pipeline?.lamp).toBe('bad')
    const degraded = progressFixture({ evaluation: { ...base.evaluation!, qa: { ...base.evaluation!.qa!, degraded: 3 } } })
    expect(deriveSignals({ progress: degraded }).pipeline?.lamp).toBe('warn')
    expect(deriveSignals({ progress: base }).pipeline).toBeUndefined()
  })

  it('待複核的常態數字不讓管線亮燈（低於門檻、抽取 needs_review 都不算）', () => {
    const base = progressFixture()
    expect(base.evaluation!.qa!.below_min).toBeGreaterThan(0)
    expect(base.extraction!.needs_review).toBeGreaterThan(0)
    expect(deriveSignals({ progress: base }).pipeline).toBeUndefined()
  })

  it('事件：進行中 → warn 並帶筆數；其中有主機資源事件時主機也亮 warn', () => {
    const s = deriveSignals({ firingIncidents: incidents('web', 'host') })
    expect(s.incidents).toMatchObject({ lamp: 'warn', count: 2 })
    expect(s.host?.lamp).toBe('warn')
    const none = deriveSignals({ firingIncidents: incidents('edge') })
    expect(none.host).toBeUndefined()
    expect(deriveSignals({ firingIncidents: incidents() }).incidents).toBeUndefined()
  })

  it('資料健康：fail → bad、warn → warn、unknown 不亮；細節列出有問題的那幾段', () => {
    expect(deriveSignals({ dataHealth: dataHealth('fail', { a: 'fail', r: 'warn' }) }).dataHealth)
      .toMatchObject({ lamp: 'bad', detail: '資料稽核、R2 對帳' })
    expect(deriveSignals({ dataHealth: dataHealth('warn', { f: 'warn' }) }).dataHealth?.lamp).toBe('warn')
    expect(deriveSignals({ dataHealth: dataHealth('unknown') }).dataHealth).toBeUndefined()
  })

  it('安全：告警觸發中 → bad；ok 與 unknown 不亮', () => {
    expect(deriveSignals({ securityAlerts: alerts('account_failures', ['account_failures']) }).security)
      .toMatchObject({ lamp: 'bad', reason: '有帳號連續登入失敗' })
    expect(deriveSignals({ securityAlerts: alerts('unknown') }).security).toBeUndefined()
  })

  it('待處理佇列只有數字，永遠不亮燈', () => {
    const s = deriveSignals({ reviewOpen: { faithfulness: 8, feedback: 3, extraction: 5 }, draftUploads: 3 })
    expect(s.reviews).toMatchObject({ count: 16 })
    expect(s.reviews?.lamp).toBeUndefined()
    expect(s.uploads).toEqual({ count: 3 })
  })

  it('待複核只回來一部分：先加已知的，沒回來的標 —；全都沒回來就沒有數字', () => {
    expect(deriveSignals({ reviewOpen: { faithfulness: 8 } }).reviews)
      .toMatchObject({ count: 8, detail: '忠實度低分 8・倒讚 —・抽取品質 —' })
    expect(deriveSignals({ reviewOpen: {} }).reviews).toBeUndefined()
  })

  it('worstLamp：有 bad 就是 bad，否則 warn，都沒有是 null；只有數字的頁不算', () => {
    const s = deriveSignals({
      services: services(svc('backup', 'important', 'failed')), firingIncidents: incidents('edge'),
      reviewOpen: { faithfulness: 8, feedback: 0, extraction: 0 },
    })
    expect(worstLamp(s, ['services', 'incidents'])).toBe('bad')
    expect(worstLamp(s, ['incidents', 'logs'])).toBe('warn')
    expect(worstLamp(s, ['reviews', 'reports'])).toBeNull()
  })
})

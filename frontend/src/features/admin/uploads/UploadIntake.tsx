import { useEffect, useRef, useState, type DragEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Card, Chip, type Tone } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { Icon } from '../../../components/primitives/Icon'
import { fmtBytes } from '../uploadLabels'
import { UPLOADS_KEY, useUploadQueue, type QueueItem, type QueueStatus } from '../useAdminUploads'
import u from '../Uploads.module.css'

/** 拖放區：整塊是一顆按鈕（點一下開檔案選擇），也收拖進來的檔案。可一次多份，只收 PDF。 */
function Dropzone({ maxBytes, onFiles }: { maxBytes: number; onFiles: (files: File[]) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const onDrop = (e: DragEvent<HTMLButtonElement>) => {
    e.preventDefault()
    setOver(false)
    const files = Array.from(e.dataTransfer?.files ?? [])
    if (files.length) onFiles(files)
  }
  return (
    <>
      <button
        type="button"
        className={`${u.dropzone} ${over ? u.dropzoneOver : ''}`}
        data-testid="upload-dropzone"
        onClick={() => inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setOver(true) }}
        onDragEnter={e => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <Icon name="upload" size={18} />
        拖放 PDF 到這裡，或點一下選擇檔案（單檔上限 {fmtBytes(maxBytes)}）
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        multiple
        hidden
        aria-label="選擇要上傳的 PDF"
        onChange={e => {
          const files = Array.from(e.target.files ?? [])
          e.target.value = '' // 同一份檔案再選一次也要觸發 change
          if (files.length) onFiles(files)
        }}
      />
    </>
  )
}

const QUEUE_LABEL: Record<QueueStatus, string> = {
  queued: '排隊中',
  uploading: '上傳中',
  done: '已上傳',
  error: '上傳失敗',
  skipped: '未上傳',
  cancelled: '已取消',
}
const QUEUE_TONE: Record<QueueStatus, Tone> = {
  queued: 'idle', uploading: 'info', done: 'ok', error: 'bad', skipped: 'bad', cancelled: 'idle',
}

function QueueRow({ item, onCancel, onOpen }: { item: QueueItem; onCancel: (id: string) => void; onOpen: (uploadId: string) => void }) {
  const pct = Math.round(item.progress * 100)
  const bad = item.status === 'error' || item.status === 'skipped'
  const { uploadId } = item
  return (
    <li className={u.queueItem} aria-label={item.name}>
      <div className={u.queueHead}>
        <span className={u.queueName}>{item.name}</span>
        <span className={`${c.muted} ${c.num}`}>{fmtBytes(item.size)}</span>
        <Chip tone={QUEUE_TONE[item.status]}>{QUEUE_LABEL[item.status]}</Chip>
        {(item.status === 'queued' || item.status === 'uploading') && (
          <Button size="sm" onClick={() => onCancel(item.id)}>取消</Button>
        )}
      </div>
      {item.status === 'uploading' && (
        <div className={u.progress} role="progressbar" aria-label={`${item.name} 上傳進度`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <div className={u.progressBar} style={{ width: `${pct}%` }} />
          <span className={u.progressText}>{pct}%</span>
        </div>
      )}
      {item.message && (
        <p className={bad ? u.queueError : u.queueNote} role={bad ? 'alert' : undefined}>
          {item.message}
          {uploadId && (
            <> <button type="button" className={c.linkBtn} onClick={() => onOpen(uploadId)}>查看這筆上傳</button></>
          )}
        </p>
      )}
    </li>
  )
}

/**
 * 收檔：拖放區＋上傳佇列。預檢、逐一上傳、取消與錯誤說明都在 `useUploadQueue`；
 * 這裡只管畫面，以及還在傳的時候擋住關分頁。
 */
export function UploadIntake({ onOpen }: { onOpen: (uploadId: string) => void }) {
  const client = useQueryClient()
  const queue = useUploadQueue({
    onUploaded: () => { void client.invalidateQueries({ queryKey: UPLOADS_KEY }) },
  })
  const { busy } = queue
  // 還有檔案在傳時關分頁要先問：離開這一頁會中止上傳（佇列只活在這一頁）。
  useEffect(() => {
    if (!busy) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [busy])
  const finished = queue.items.some(it => it.status !== 'queued' && it.status !== 'uploading')
  return (
    <>
      <Dropzone maxBytes={queue.maxBytes} onFiles={queue.addFiles} />
      {queue.items.length > 0 && (
        <Card title="上傳佇列" labelledBy="upload-queue-title"
          hint={busy ? '上傳中請留在這一頁，離開會中止還沒完成的上傳。' : undefined}
          right={finished ? <Button size="sm" onClick={queue.clearFinished}>清除已結束的項目</Button> : undefined}>
          <ul className={u.queue} aria-label="上傳佇列">
            {queue.items.map(it => <QueueRow key={it.id} item={it} onCancel={queue.cancel} onOpen={onOpen} />)}
          </ul>
        </Card>
      )}
    </>
  )
}

/**
 * 管理後台主控台的共用元件。各頁一律從這裡 import，樣式類名另外
 * `import c from '.../components/console/Console.module.css'`。
 */
export { ConsolePage } from './ConsolePage'
export { PageHeader } from './PageHeader'
export { Button, Card, Chip, Field, SearchInput, Segmented, StatTile, Status, Tabs, type SegOption, type Tone } from './controls'
export { ConfirmDialog, Dialog, FormDialog } from './Dialog'
export { Drawer, DrawerFootNote, DrawerSection } from './Drawer'
export { RowActions, type RowAction } from './RowActions'
export { Toaster } from './Toaster'
export { dismissToast, toast, TOAST_MS } from './toastStore'
export { TrendBars, type TrendBar } from './TrendBars'
export { ElevationCancelledError } from '../../features/account/useElevationGate'
export { isElevationCancelled, useElevate } from './useElevate'
export { useQueryParam } from './useQueryParam'
export { Lamp, type LampTone } from '../primitives/Lamp'

import type { ReactNode, SVGProps } from 'react'

export type IconName =
  | 'search' | 'messages' | 'activity' | 'user' | 'plus'
  | 'panel' | 'logout' | 'chevronDown' | 'x' | 'filter'
  | 'alertCircle' | 'alertTriangle' | 'send' | 'fileText' | 'trash' | 'thumbUp' | 'thumbDown' | 'copy'
  | 'check' | 'spinner' | 'refresh'
  | 'compass' | 'trendUp' | 'trendDown' | 'trendFlat' | 'diverge' | 'notComparable' | 'quote' | 'info'
  | 'minus' | 'download' | 'arrowsHorizontal'

const PATHS: Record<IconName, ReactNode> = {
  search: (<><circle cx="10" cy="10" r="7" /><path d="M21 21l-6 -6" /></>),
  messages: (<><path d="M8 9h8" /><path d="M8 13h5" /><path d="M18 4a3 3 0 0 1 3 3v8a3 3 0 0 1 -3 3h-5l-5 3v-3h-2a3 3 0 0 1 -3 -3v-8a3 3 0 0 1 3 -3z" /></>),
  activity: (<path d="M3 12h4l3 8l4 -16l3 8h4" />),
  user: (<><circle cx="12" cy="8" r="4" /><path d="M6 21v-1a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4v1" /></>),
  plus: (<path d="M12 5v14M5 12h14" />),
  panel: (<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>),
  logout: (<><path d="M14 8V6a2 2 0 0 0 -2 -2H6a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h6a2 2 0 0 0 2 -2v-2" /><path d="M9 12h12l-3 -3M18 15l3 -3" /></>),
  chevronDown: (<path d="M6 9l6 6l6 -6" />),
  x: (<path d="M18 6l-12 12M6 6l12 12" />),
  filter: (<path d="M4 6h16M7 12h10M10 18h4" />),
  alertCircle: (<><circle cx="12" cy="12" r="9" /><path d="M12 8v4M12 16h.01" /></>),
  alertTriangle: (<><path d="M12 3l9 16H3z" /><path d="M12 10v4M12 17h.01" /></>),
  send: (<><path d="M12 20V5" /><path d="M6 11l6 -6l6 6" /></>),
  fileText: (<><path d="M14 3H7a2 2 0 0 0 -2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2V8z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></>),
  trash: (<><path d="M4 7h16M9 7V5a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v2M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1 -1l1 -12" /></>),
  thumbUp: (<><path d="M7 11v9" /><path d="M11 11l1.4 -4.2a1.5 1.5 0 0 1 3 .5v3.7h3.6a1.6 1.6 0 0 1 1.6 1.9l-1.2 5.5a1.6 1.6 0 0 1 -1.6 1.2H7v-9z" /></>),
  thumbDown: (<><path d="M17 13v-9" /><path d="M13 13l-1.4 4.2a1.5 1.5 0 0 1 -3 -.5v-3.7H5a1.6 1.6 0 0 1 -1.6 -1.9l1.2 -5.5a1.6 1.6 0 0 1 1.6 -1.2H17v9z" /></>),
  copy: (<><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2 -2h10" /></>),
  check: (<path d="M5 12l5 5l9 -11" />),
  spinner: (<path d="M12 3a9 9 0 1 0 9 9" />),
  refresh: (<><path d="M20 11a8.1 8.1 0 0 0 -15.5 -2M4.5 5v4h4" /><path d="M4 13a8.1 8.1 0 0 0 15.5 2M19.5 19v-4h-4" /></>),
  compass: (<><circle cx="12" cy="12" r="9" /><path d="M14.5 9.5l-2 5l-5 2l2 -5z" /><circle cx="12" cy="12" r="1" /></>),
  trendUp: (<path d="M4 16l6 -6l4 4l6 -6M14 8h6v6" />),
  trendDown: (<path d="M4 8l6 6l4 -4l6 6M14 16h6v-6" />),
  trendFlat: (<path d="M4 12h16M16 8l4 4l-4 4" />),
  diverge: (<><path d="M12 4v16" /><path d="M6 9l6 -5l6 5" /><path d="M6 15l6 5l6 -5" /></>),
  notComparable: (<><circle cx="12" cy="12" r="9" /><path d="M8 12h8" /></>),
  quote: (<path d="M8 13h2a2 2 0 0 0 2 -2V9a2 2 0 0 0 -2 -2H8a2 2 0 0 0 -2 2v2c0 3 2 5 4 6M16 13h2a2 2 0 0 0 2 -2V9a2 2 0 0 0 -2 -2h-2a2 2 0 0 0 -2 2v2c0 3 2 5 4 6" />),
  info: (<><circle cx="12" cy="12" r="9" /><path d="M12 10v6M12 7h.01" /></>),
  minus: (<path d="M5 12h14" />),
  download: (<><path d="M12 4v12" /><path d="M8 12l4 4l4 -4" /><path d="M4 19h16" /></>),
  arrowsHorizontal: (<path d="M7 8l-4 4l4 4M17 8l4 4l-4 4M4 12h16" />),
}

interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName
  size?: number
}

export function Icon({ name, size = 20, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {PATHS[name]}
    </svg>
  )
}

import type { ReactNode, SVGProps } from 'react'

export type IconName =
  | 'search' | 'messages' | 'activity' | 'user' | 'plus'
  | 'panel' | 'logout' | 'chevronDown' | 'x'

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

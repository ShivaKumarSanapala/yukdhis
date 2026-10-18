import type { ReactNode } from "react";

const PATHS: Record<string, ReactNode> = {
  analyze: <path d="M13 2 4 14h7l-1 8 9-12h-7z" />,
  reports: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></>,
  graph: <><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" /><path d="m8.2 10.8 7.6-3.6M8.2 13.2l7.6 3.6" /></>,
  shield: <><path d="m12 3 8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z" /><path d="m9 12 2 2 4-4" /></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  signout: <><path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4" /><path d="m16 8 4 4-4 4M20 12H9" /></>,
  sparkle: <path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z" />,
  edit: <path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4" />,
  trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  plus: <path d="M12 5v14M5 12h14" />,
  send: <path d="M12 19V5M6 11l6-6 6 6" />,
  stop: <rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" />,
  chat: <path d="M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-5 4v-4H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" />,
  search: <><circle cx="11" cy="11" r="6" /><path d="m20 20-4.2-4.2" /></>,
  panel: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M9.5 4.5v15" /></>,
  attach: <path d="m20 11.5-8.2 8.2a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8" />,
  link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  file: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></>,
  more: <><circle cx="5" cy="12" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /><circle cx="19" cy="12" r="1.2" fill="currentColor" /></>,
  down: <path d="m6 9 6 6 6-6" />,
  arrowDown: <path d="M12 5v14M6 13l6 6 6-6" />,
  inbox: <><path d="M4 13h4l1.5 3h5L16 13h4" /><path d="M5.5 5h13L21 13v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5z" /></>,
  share: <><path d="M12 15V4M8 8l4-4 4 4" /><path d="M5 13v6h14v-6" /></>,
  compare: <path d="M8 4v16M16 4v16M4 8l4-4 4 4M12 16l4 4 4-4" />,
  refresh: <><path d="M20 12a8 8 0 1 1-2.5-5.8" /><path d="M20 4v5h-5" /></>,
  user: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" /></>,
  expand: <path d="m5 9 7 7 7-7" />,
};

/** The Anuvi mark: a four-point sparkle whose tips are knowledge-graph nodes. Decorative. */
export function BrandMark({ size = 18 }: { size?: number }) {
  return (
    <svg className="icon-svg" width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
      <path d="M16 5Q16 16 27 16Q16 16 16 27Q16 16 5 16Q16 16 16 5Z" fill="currentColor" fillOpacity=".2" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M16 5V27M5 16H27" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeOpacity=".85" />
      <g fill="currentColor"><circle cx="16" cy="5" r="2.4" /><circle cx="27" cy="16" r="2.4" /><circle cx="16" cy="27" r="2.4" /><circle cx="5" cy="16" r="2.4" /><circle cx="16" cy="16" r="3.1" /></g>
    </svg>
  );
}

/** Inline 24px stroke icon (no icon library). Decorative: always aria-hidden. */
export default function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg className="icon-svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{PATHS[name]}</svg>
  );
}

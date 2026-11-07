import type { SVGProps } from "react";

export type IconName =
  | "overview"
  | "progress"
  | "history"
  | "pressure"
  | "composition"
  | "download"
  | "refresh"
  | "sparkle"
  | "calendar"
  | "scale"
  | "heart"
  | "activity"
  | "clock"
  | "arrow"
  | "ruler"
  | "steps"
  | "waves"
  | "moon"
  | "flask"
  | "scan"
  | "stethoscope"
  | "journal"
  | "shield"
  | "more"
  | "close"
  | "plus"
  | "trophy"
  | "flame"
  | "pause"
  | "trash";

const paths: Record<IconName, JSX.Element> = {
  overview: <><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
  progress: <><path d="M3 19 9 13l4 4 8-10"/><path d="M15 7h6v6"/></>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></>,
  pressure: <><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z"/><path d="m7 12 2-2 2 4 2-5 2 3h3"/></>,
  composition: <><circle cx="12" cy="8" r="4"/><path d="M5.5 21a6.5 6.5 0 0 1 13 0M8 8h8M12 4v8"/></>,
  download: <><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 17v3h16v-3"/></>,
  refresh: <><path d="M20 7h-5V2"/><path d="M20 7a8 8 0 1 0 1 7"/></>,
  sparkle: <><path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2L12 3Z"/><path d="m5 14 .8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8L5 14Zm14-1 .6 1.4L21 15l-1.4.6L19 17l-.6-1.4L17 15l1.4-.6L19 13Z"/></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M16 3v4M8 3v4M3 10h18"/></>,
  scale: <><rect x="3" y="4" width="18" height="17" rx="4"/><path d="M8 10a4 4 0 0 1 8 0M12 10l2-2"/></>,
  heart: <><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z"/></>,
  activity: <><path d="M3 12h4l2-6 4 12 2-6h6"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
  ruler: <><rect x="2.5" y="8" width="19" height="8" rx="2"/><path d="M6.5 8v3M10 8v4M13.5 8v3M17 8v4"/></>,
  steps: <><path d="M7.5 3.5c1.8 0 2.8 2 2.6 4.4-.2 2.1-.8 3.6-2.6 3.6S5 10 4.9 7.9C4.8 5.5 5.7 3.5 7.5 3.5Z"/><path d="M5.5 14h4l-.3 2.4a1.9 1.9 0 0 1-3.8-.1Z"/><path d="M16.5 7.5c1.8 0 2.7 2 2.6 4.4-.1 2.1-.8 3.6-2.6 3.6s-2.5-1.5-2.6-3.6c-.2-2.4.8-4.4 2.6-4.4Z"/><path d="M14.5 18h4l-.3 1.4a1.9 1.9 0 0 1-3.8-.1Z"/></>,
  waves: <><path d="M2 8c2 0 2-1.5 4-1.5S8 8 10 8s2-1.5 4-1.5S16 8 18 8s2-1.5 4-1.5"/><path d="M2 13c2 0 2-1.5 4-1.5S8 13 10 13s2-1.5 4-1.5 2 1.5 4 1.5 2-1.5 4-1.5"/><path d="M2 18c2 0 2-1.5 4-1.5S8 18 10 18s2-1.5 4-1.5 2 1.5 4 1.5 2-1.5 4-1.5"/></>,
  moon: <><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/></>,
  flask: <><path d="M9 3h6M10 3v6l-5.4 9.2A2 2 0 0 0 6.3 21h11.4a2 2 0 0 0 1.7-2.8L14 9V3"/><path d="M7.5 15h9"/></>,
  scan: <><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M8 12h8M12 8v8"/></>,
  stethoscope: <><path d="M5 3v5a5 5 0 0 0 10 0V3"/><path d="M10 13v2a5 5 0 0 0 10 0v-1"/><circle cx="20" cy="12" r="2"/></>,
  journal: <><path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v18H6.5A1.5 1.5 0 0 1 5 19.5Z"/><path d="M5 18.5A1.5 1.5 0 0 1 6.5 17H19M9 7.5h6M9 11h4"/></>,
  shield: <><path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.2 7.5 9.5 4.3-1.3 7.5-4.9 7.5-9.5V6Z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/></>,
  more: <><circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/></>,
  close: <><path d="M6 6l12 12M18 6 6 18"/></>,
  plus: <><path d="M12 5v14M5 12h14"/></>,
  trophy: <><path d="M8 4h8v5a4 4 0 0 1-8 0Z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8.5 20h7M10 17h4"/></>,
  flame: <><path d="M12 21c3.9 0 6.5-2.6 6.5-6.2 0-3.6-2.6-5.6-3.6-8.8-2.2 1.6-2.4 3.9-2.4 3.9S10.5 7 11 3c-3.8 2.4-5.5 6.5-5.5 11.6C5.5 18.4 8.1 21 12 21Z"/></>,
  pause: <><rect x="6.5" y="5" width="3.5" height="14" rx="1"/><rect x="14" y="5" width="3.5" height="14" rx="1"/></>,
  trash: <><path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l.8 12.5a1.6 1.6 0 0 0 1.6 1.5h6.2a1.6 1.6 0 0 0 1.6-1.5L17.5 7"/></>,
};

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {paths[name]}
    </svg>
  );
}

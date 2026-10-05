import type { ReactNode } from "react";

/** A handful of 20px line icons; stroke follows `currentColor`. */
function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="20"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.7"
      viewBox="0 0 20 20"
      width="20"
    >
      {children}
    </svg>
  );
}

export const PlusIcon = () => (
  <Icon>
    <path d="M10 4v12M4 10h12" />
  </Icon>
);

export const OverviewIcon = () => (
  <Icon>
    <rect height="6" rx="1.5" width="6" x="3" y="3" />
    <rect height="6" rx="1.5" width="6" x="11" y="3" />
    <rect height="6" rx="1.5" width="6" x="3" y="11" />
    <rect height="6" rx="1.5" width="6" x="11" y="11" />
  </Icon>
);

export const ImportIcon = () => (
  <Icon>
    <path d="M10 3v9M6.5 8.5 10 12l3.5-3.5M4 14v2h12v-2" />
  </Icon>
);

export const AnnotateIcon = () => (
  <Icon>
    <path d="M4 16l1-4 8-8 3 3-8 8-4 1zM11.5 5.5l3 3" />
  </Icon>
);

export const SettingsIcon = () => (
  <Icon>
    <path d="M4 6h8M15 6h1M4 14h1M8 14h8" />
    <circle cx="13.5" cy="6" r="1.5" />
    <circle cx="6.5" cy="14" r="1.5" />
  </Icon>
);

export const ExportIcon = () => (
  <Icon>
    <path d="M10 12V3M6.5 6.5 10 3l3.5 3.5M4 14v2h12v-2" />
  </Icon>
);

export const CollapseIcon = ({ collapsed }: { collapsed: boolean }) => (
  <Icon>
    <rect height="14" rx="2" width="14" x="3" y="3" />
    <path d="M8 3v14" />
    <path d={collapsed ? "M11 8.5l1.5 1.5L11 11.5" : "M13 8.5 11.5 10 13 11.5"} />
  </Icon>
);

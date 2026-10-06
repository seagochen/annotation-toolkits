import { Icon } from "../../../components/shell/icons";

/** Line icons for the workspace's panel tabs and canvas tool rail. */

export const SelectIcon = () => (
  <Icon>
    <path d="M5 3l10 6.5-4.5 1L8.5 15z" />
  </Icon>
);

export const HandIcon = () => (
  <Icon>
    <path d="M7 10V4.5a1.25 1.25 0 0 1 2.5 0V9M9.5 8.5V3.5a1.25 1.25 0 0 1 2.5 0V9M12 9V4.5a1.25 1.25 0 0 1 2.5 0V11c0 3.3-2.2 6-5.5 6-2.1 0-3.4-1-4.6-2.7L2.6 11.6a1.3 1.3 0 0 1 2-1.6L7 12.5" />
  </Icon>
);

export const BoxIcon = () => (
  <Icon>
    <rect height="12" rx="1" width="12" x="4" y="4" />
    <path d="M2.5 4h3M14.5 4h3M2.5 16h3M14.5 16h3" />
  </Icon>
);

export const PolygonIcon = () => (
  <Icon>
    <path d="M4 7l6-4 6 5-2 8H6z" />
    <circle cx="4" cy="7" r="1.2" />
    <circle cx="10" cy="3" r="1.2" />
    <circle cx="16" cy="8" r="1.2" />
    <circle cx="14" cy="16" r="1.2" />
    <circle cx="6" cy="16" r="1.2" />
  </Icon>
);

export const BrushIcon = () => (
  <Icon>
    <path d="M16.5 3.5 9 11l-1.5-1.5L15 2zM7.5 11.5c-2 0-3 1.2-3 3 0 1-.7 1.7-1.5 2 3.5.8 6-.5 6-3.2z" />
  </Icon>
);

export const EraserIcon = () => (
  <Icon>
    <path d="M8 16h9M3.7 12.3l7.6-7.6a1.5 1.5 0 0 1 2.1 0l2.9 2.9a1.5 1.5 0 0 1 0 2.1L10 16H7.4l-3.7-3.7z" />
    <path d="M7 9l4 4" />
  </Icon>
);

export const RaiseIcon = () => (
  <Icon>
    <path d="M10 16V4M5.5 8.5 10 4l4.5 4.5" />
  </Icon>
);

export const LowerIcon = () => (
  <Icon>
    <path d="M10 4v12M5.5 11.5 10 16l4.5-4.5" />
  </Icon>
);

export const UndoIcon = () => (
  <Icon>
    <path d="M7 5 3.5 8.5 7 12" />
    <path d="M3.5 8.5H12a4.5 4.5 0 0 1 0 9H9" />
  </Icon>
);

export const RedoIcon = () => (
  <Icon>
    <path d="M13 5l3.5 3.5L13 12" />
    <path d="M16.5 8.5H8a4.5 4.5 0 0 0 0 9h3" />
  </Icon>
);

export const LabelsIcon = () => (
  <Icon>
    <path d="M3 4.5A1.5 1.5 0 0 1 4.5 3H10l7 7-7 7-7-7z" />
    <circle cx="7" cy="7" r="1.3" />
  </Icon>
);

export const KeyboardIcon = () => (
  <Icon>
    <rect height="10" rx="2" width="16" x="2" y="5" />
    <path d="M5.5 8.5h1M9.5 8.5h1M13.5 8.5h1M6.5 12h7" />
  </Icon>
);

export const CodeIcon = () => (
  <Icon>
    <path d="M7 6 3 10l4 4M13 6l4 4-4 4M11 4.5 9 15.5" />
  </Icon>
);

export const ChevronLeftIcon = () => (
  <Icon>
    <path d="M12 5l-5 5 5 5" />
  </Icon>
);

export const ChevronRightIcon = () => (
  <Icon>
    <path d="M8 5l5 5-5 5" />
  </Icon>
);

export const BackIcon = () => (
  <Icon>
    <path d="M16 10H4M9 5l-5 5 5 5" />
  </Icon>
);

export const DocumentIcon = () => (
  <Icon>
    <path d="M5 2.5h6.5L15 6v11.5H5z" />
    <path d="M11.5 2.5V6H15M7.5 10h5M7.5 13h5" />
  </Icon>
);

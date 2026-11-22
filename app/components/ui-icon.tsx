import type { SVGProps } from "react";

const paths = {
  studio: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  film: "M4 4h16v16H4zM8 4v16M16 4v16M4 9h4m-4 6h4m8-6h4m-4 6h4",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5",
  download: "M12 3v13m-5-5 5 5 5-5M4 16v4h16v-4",
  cut: "m14 4-4 16M7 4H3v5m0 6v5h4M17 4h4v5m0 6v5h-4",
  captions: "M3 5h18v14H3zM10 10H7v4h3m7-4h-3v4h3",
  sparkles: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  local: "M4 4h16v12H4zM8 20h8m-4-4v4m-4-9 3 3 5-5",
} as const;

export function Icon({
  name,
  ...props
}: SVGProps<SVGSVGElement> & { name: keyof typeof paths }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d={paths[name]} />
    </svg>
  );
}

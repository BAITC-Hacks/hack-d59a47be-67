/** Original vector motif inspired by the mirrored ram-horn shapes of Kazakh ornament. */
export function Ornament({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`qoshqar-ornament ${className}`}
      viewBox="0 0 240 80"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {[0, 80, 160].map((offset) => (
        <g key={offset} transform={`translate(${offset} 0)`}>
          <path d="M40 71V42C40 23 18 9 10 25C3 40 15 50 25 40C32 33 27 23 20 28M40 42C40 23 62 9 70 25C77 40 65 50 55 40C48 33 53 23 60 28" />
          <path d="M17 65C30 65 32 55 40 50C48 55 50 65 63 65M33 12L40 5L47 12L40 19Z" />
          <path d="M0 72H25L40 60L55 72H80" strokeWidth="1.5" />
        </g>
      ))}
    </svg>
  );
}

export function QuestMark() {
  return (
    <svg viewBox="0 0 48 48" fill="none" aria-hidden="true" focusable="false">
      <path
        d="M24 3L30 9H39V18L45 24L39 30V39H30L24 45L18 39H9V30L3 24L9 18V9H18L24 3Z"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="M24 35V24C24 15 15 12 12 18C9 24 17 28 19 22M24 24C24 15 33 12 36 18C39 24 31 28 29 22"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <path
        d="M18 35L24 29L30 35"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

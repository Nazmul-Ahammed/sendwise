export default function Logo({ size = 36 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" style={{ flexShrink: 0 }} aria-label="Sendwise logo">
      <defs>
        <linearGradient id="swg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6366F1" />
          <stop offset="1" stopColor="#8B5CF6" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="12" fill="url(#swg)" />
      <rect x="11" y="15" width="26" height="19" rx="3.5" fill="none" stroke="#fff" strokeWidth="2.2" />
      <path d="M12.5 17.5 L24 26 L35.5 17.5" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M25.6 8.5 L19.4 18.4 H23.9 L21.6 27 L28.9 16.6 H24.2 Z" fill="#FDE047" />
    </svg>
  );
}

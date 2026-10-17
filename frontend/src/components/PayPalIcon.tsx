/** Minimal PayPal "P" mark. */
export function PayPalIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden>
      <path fill="#003087" d="M8.3 21.5H4.6a.6.6 0 0 1-.6-.7L6.9 2.6c.1-.6.6-1.1 1.2-1.1h6.6c3.4 0 5.6 1.8 5 5.2-.7 4.2-3.5 6-7.2 6H10l-1 6.9a1 1 0 0 1-.7.9Z" />
      <path fill="#0079C1" d="M19.8 7.3c-.7 4-3.4 5.9-7.1 5.9h-1.6c-.5 0-.9.4-1 .9l-1 6.4-.2 1.2a.5.5 0 0 0 .5.6h3.2c.4 0 .8-.3.9-.8l.6-3.8c.1-.4.5-.8.9-.8h.6c3.2 0 5.6-1.3 6.3-5 .3-1.6.1-2.9-.8-3.8l-.3-.8Z" />
    </svg>
  );
}

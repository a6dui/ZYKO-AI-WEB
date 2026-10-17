/**
 * PLAN B: Premium Vector Duotone / Neon SVG Studio Icons (Linear / Vercel / Raycast style)
 * Clean, lightweight, scalable, with emerald, amber, and violet gradients.
 * Activated on user request "делай план б".
 */
const PLAN_B_STUDIO_ICONS = {
  trends: `
    <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="trend-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#f59e0b"/>
          <stop offset="100%" stop-color="#ef4444"/>
        </linearGradient>
      </defs>
      <path d="M12 2c1.5 3.5 4 5.5 4 9 0 3.3-2.7 6-6 6s-6-2.7-6-6c0-3.5 2.5-5.5 4-9z" fill="url(#trend-grad)" fill-opacity="0.25" stroke="url(#trend-grad)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M12 11c.8 1.5 2 2.5 2 4a2 2 0 1 1-4 0c0-1.5 1.2-2.5 2-4z" fill="#f59e0b"/>
    </svg>
  `,
  agents: `
    <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="agent-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#10b981"/>
          <stop offset="100%" stop-color="#06b6d4"/>
        </linearGradient>
      </defs>
      <circle cx="12" cy="12" r="8" stroke="url(#agent-grad)" stroke-width="1.8" stroke-dasharray="2 3"/>
      <circle cx="12" cy="12" r="4" fill="url(#agent-grad)" fill-opacity="0.3" stroke="url(#agent-grad)" stroke-width="1.8"/>
      <circle cx="12" cy="12" r="1.5" fill="#10b981"/>
    </svg>
  `,
  text: `
    <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="llm-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#8b5cf6"/>
          <stop offset="100%" stop-color="#3b82f6"/>
        </linearGradient>
      </defs>
      <rect x="4" y="4" width="16" height="16" rx="4" stroke="url(#llm-grad)" stroke-width="1.8" fill="url(#llm-grad)" fill-opacity="0.2"/>
      <path d="M9 9h6M9 12h6M9 15h3" stroke="#a78bfa" stroke-width="1.8" stroke-linecap="round"/>
      <circle cx="17" cy="15" r="1" fill="#60a5fa"/>
    </svg>
  `,
  image: `
    <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="img-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#06b6d4"/>
          <stop offset="100%" stop-color="#10b981"/>
        </linearGradient>
      </defs>
      <rect x="3" y="3" width="18" height="18" rx="4" stroke="url(#img-grad)" stroke-width="1.8" fill="url(#img-grad)" fill-opacity="0.15"/>
      <circle cx="8.5" cy="8.5" r="2" fill="#06b6d4"/>
      <path d="M21 16l-5.5-5.5-8.5 8.5" stroke="url(#img-grad)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  `,
  video: `
    <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="vid-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#ec4899"/>
          <stop offset="100%" stop-color="#8b5cf6"/>
        </linearGradient>
      </defs>
      <rect x="3" y="4" width="18" height="16" rx="3" stroke="url(#vid-grad)" stroke-width="1.8" fill="url(#vid-grad)" fill-opacity="0.2"/>
      <path d="M10 9l5 3-5 3V9z" fill="url(#vid-grad)"/>
    </svg>
  `,
  audio: `
    <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="aud-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#10b981"/>
          <stop offset="100%" stop-color="#34d399"/>
        </linearGradient>
      </defs>
      <path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 11v2" stroke="url(#aud-grad)" stroke-width="2" stroke-linecap="round"/>
    </svg>
  `,
  toolkit: `
    <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="tool-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#f59e0b"/>
          <stop offset="100%" stop-color="#10b981"/>
        </linearGradient>
      </defs>
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" stroke="url(#tool-grad)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="url(#tool-grad)" fill-opacity="0.15"/>
    </svg>
  `
};

if (typeof window !== 'undefined') {
  window.PLAN_B_STUDIO_ICONS = PLAN_B_STUDIO_ICONS;
}

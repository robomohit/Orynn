## 2026-04-21 - [Clean Stream UI]
**Learning:** Text-based icons (`=` and `x`) paired with missing `aria-label`s hurt both aesthetic consistency and screen reader usability. Bulky box-shadows and thick borders on chat/stream UI components often contribute to cognitive overload.
**Action:** Always replace purely structural text pseudo-icons with semantic SVGs that gracefully handle color-inversion and hover states. When updating streaming UIs (like chat), default to flatter layouts (removing heavy borders and drop shadows) to emulate modern clean patterns (e.g. Claude Code) while maintaining distinct backgrounds to separate context zones.
## 2026-05-16 - [Semantic SVG Icons for Micro-Interactions]
**Learning:** Replaced crude text pseudo-icons (like , , , , ) with crisp, color-coordinated inline SVGs in components like toasts, modals, and history buttons. This prevents issues with screen readers reading out odd characters and solves visual alignment issues with mixed icon/text layouts.
**Action:** Consistently use inline SVGs with appropriate  or  attributes rather than text/emoji characters for interactive UI elements to guarantee both visual consistency and accessibility.
## 2026-05-16 - [Semantic SVG Icons for Micro-Interactions]
**Learning:** Replaced crude text pseudo-icons (like `✕`, `✓`, `⚠`, `↻`, `›`) with crisp, color-coordinated inline SVGs in components like toasts, modals, and history buttons. This prevents issues with screen readers reading out odd characters and solves visual alignment issues with mixed icon/text layouts.
**Action:** Consistently use inline SVGs with appropriate `aria-label` or `aria-hidden="true"` attributes rather than text/emoji characters for interactive UI elements to guarantee both visual consistency and accessibility.

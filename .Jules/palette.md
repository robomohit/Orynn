## 2026-04-21 - [Clean Stream UI]
**Learning:** Text-based icons (`=` and `x`) paired with missing `aria-label`s hurt both aesthetic consistency and screen reader usability. Bulky box-shadows and thick borders on chat/stream UI components often contribute to cognitive overload.
**Action:** Always replace purely structural text pseudo-icons with semantic SVGs that gracefully handle color-inversion and hover states. When updating streaming UIs (like chat), default to flatter layouts (removing heavy borders and drop shadows) to emulate modern clean patterns (e.g. Claude Code) while maintaining distinct backgrounds to separate context zones.
## 2025-05-03 - [Semantic Labels and ARIA for Inputs]
**Learning:** Found non-semantic `<div>` elements acting as labels (`<div class="selector-label">`) and standalone search/command inputs missing explicit screen reader support.
**Action:** Use semantic `<label for="[id]">` tags for form inputs and provide explicit `aria-label` attributes for standalone interactive inputs (like search bars and command palettes) to ensure robust screen reader accessibility.

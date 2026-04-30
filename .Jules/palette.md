## 2026-04-21 - [Clean Stream UI]
**Learning:** Text-based icons (`=` and `x`) paired with missing `aria-label`s hurt both aesthetic consistency and screen reader usability. Bulky box-shadows and thick borders on chat/stream UI components often contribute to cognitive overload.
**Action:** Always replace purely structural text pseudo-icons with semantic SVGs that gracefully handle color-inversion and hover states. When updating streaming UIs (like chat), default to flatter layouts (removing heavy borders and drop shadows) to emulate modern clean patterns (e.g. Claude Code) while maintaining distinct backgrounds to separate context zones.

## 2026-04-21 - [Semantic Form Elements]
**Learning:** Utilizing semantic elements like <label for="..."> and explicit aria-labels on inputs drastically improves accessibility for screen readers compared to structural divs, a common issue in dynamic UIs.
**Action:** Always favor <label for="..."> over unstructured text descriptions for inputs, and provide aria-labels for standalone dynamic inputs without visible labels.

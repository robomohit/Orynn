## 2026-04-21 - [Clean Stream UI]
**Learning:** Text-based icons (`=` and `x`) paired with missing `aria-label`s hurt both aesthetic consistency and screen reader usability. Bulky box-shadows and thick borders on chat/stream UI components often contribute to cognitive overload.
**Action:** Always replace purely structural text pseudo-icons with semantic SVGs that gracefully handle color-inversion and hover states. When updating streaming UIs (like chat), default to flatter layouts (removing heavy borders and drop shadows) to emulate modern clean patterns (e.g. Claude Code) while maintaining distinct backgrounds to separate context zones.

## 2024-05-20 - [Semantic Input Labels]
**Learning:** Using generic HTML tags like `<div>` or `<span>` to position descriptive text next to inputs creates an accessibility gap. Screen readers fail to associate the label text with the interactive control, forcing users to guess the input's purpose.
**Action:** Always wrap input descriptions in semantic `<label for="[input-id]">` elements instead of styling structural tags. For inputs that stand alone without visible text (like search bars or command palettes), provide an explicit `aria-label`. This guarantees screen readers correctly interpret the form elements.

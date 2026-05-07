## 2026-04-21 - [Clean Stream UI]
**Learning:** Text-based icons (`=` and `x`) paired with missing `aria-label`s hurt both aesthetic consistency and screen reader usability. Bulky box-shadows and thick borders on chat/stream UI components often contribute to cognitive overload.
**Action:** Always replace purely structural text pseudo-icons with semantic SVGs that gracefully handle color-inversion and hover states. When updating streaming UIs (like chat), default to flatter layouts (removing heavy borders and drop shadows) to emulate modern clean patterns (e.g. Claude Code) while maintaining distinct backgrounds to separate context zones.

## 2026-04-22 - [Form Accessibility]
**Learning:** `<div>` or `<span>` elements used as labels for form inputs hurt screen reader usability because they do not semantically bind to the input, and standalone inputs lack context.
**Action:** Always use semantic `<label for="[id]">` elements instead of `<div>` or `<span>` for form input descriptions, adding `display: block;` to CSS if converting from a block-level element to preserve vertical layout. Provide explicit `aria-label` attributes for standalone inputs to ensure screen reader accessibility.

## 2026-04-21 - [Clean Stream UI]
**Learning:** Text-based icons (`=` and `x`) paired with missing `aria-label`s hurt both aesthetic consistency and screen reader usability. Bulky box-shadows and thick borders on chat/stream UI components often contribute to cognitive overload.
**Action:** Always replace purely structural text pseudo-icons with semantic SVGs that gracefully handle color-inversion and hover states. When updating streaming UIs (like chat), default to flatter layouts (removing heavy borders and drop shadows) to emulate modern clean patterns (e.g. Claude Code) while maintaining distinct backgrounds to separate context zones.

## 2026-04-22 - [Form Controls Accessibility]
**Learning:** Using `<div>` elements as visual labels for inputs (like the Model, Mode, and Target App selectors) and omitting `aria-label` attributes on standalone search inputs or textareas prevents screen readers from properly associating context with the inputs.
**Action:** Always use semantic `<label for="[id]">` elements instead of `<div>` or `<span>` for form input descriptions, and provide explicit `aria-label` attributes for standalone inputs to ensure screen reader accessibility.

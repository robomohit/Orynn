# AI Computer Design System Spec (Stitch-Ready)

This document specifies the design system for **AI Computer**, an autonomous agentic workspace. The aesthetic is inspired by the "Claude Desktop" and "Cursor" design language: clean, minimalist, high-fidelity, and focused on clarity and speed.

## 1. Brand Identity
- **Personality**: Professional, precise, minimalist, and intelligent.
- **Tone**: Quiet and focused. UI elements should fade away to let the agent-user conversation lead.
- **Visual Style**: High-fidelity with soft elevations, glassmorphic accents, and a monochrome-first palette with selective vibrant highlights.

## 2. Design Tokens

### Color Palette (Material 3 Inspired)
- **Primary (Action)**: `#1a73e8` (Google Blue / Deep Indigo)
- **On-Primary**: `#ffffff`
- **Surface**: `#ffffff` (Pure white for cards/input)
- **Background**: `#f8f9fa` (Soft neutral gray for the main workspace)
- **Surface Variant**: `#f1f3f4` (Slightly darker for sidebar/secondary areas)
- **Outline**: `#dadce0` (Thin, subtle borders)
- **On-Surface (Ink)**: `#202124` (Deep gray, not pure black)
- **On-Surface Variant (Muted)**: `#5f6368` (Lighter gray for meta-info)

### Typography
- **Primary Sans**: `Outfit` or `Inter` (Fallback: `system-ui`)
  - *Headings*: 500-600 weight, tight tracking.
  - *Body*: 400 weight, 1.5-1.6 line-height.
- **Monospace (Code/Logs)**: `JetBrains Mono` or `Fira Code`
  - *Usage*: Action arguments, terminal output, file paths, and logs.

### Elevation & Shadows
- **Level 1 (Cards)**: `0 1px 3px 0 rgba(60,64,67,.3), 0 1px 3px 1px rgba(60,64,67,.15)`
- **Level 2 (Input/Popovers)**: `0 4px 4px 0 rgba(60,64,67,.3), 0 8px 12px 6px rgba(60,64,67,.15)`
- **Glassmorphism**: `backdrop-filter: blur(40px) saturate(180%); background: rgba(255, 255, 255, 0.7);`

## 3. Core Components

### Sidebar (Navigation & History)
- **Style**: Glassmorphic / Frosted.
- **Items**: Active items use a soft pill background (`--md-sys-color-primary-container`).
- **Icons**: Minimalist, stroke-based (2px weight).

### Chat Stream (The Feed)
- **User Message**:
  - Rounded pill shape (`20px 4px 20px 20px`).
  - Background: Primary Indigo (`#1a73e8`).
  - Font: Semi-bold, white text.
- **Agent Message**:
  - Borderless layout or very subtle white card.
  - No side lines or dots.
  - Content flows naturally with ample padding.

### Tool Cards (Action/Result)
- **Container**: White surface with a thin border and soft shadow.
- **Header**: Compact, using JetBrains Mono for the tool name.
- **Collapsibility**: Accordion style with a rotating chevron.
- **Auto-Expansion**: Cards should "pop" open when new logs or results arrive.

### Subtask Items
- **Status Icons**: Square or slightly rounded (8px radius).
- **Colors**: Neutral gray for pending, Pulse blue for running, Solid Green (`#1e8e3e`) for success.
- **Typography**: Subtask descriptions should be clear and concise.

### Composer (Input Area)
- **Shape**: Large rounded rectangle (24px radius).
- **Behavior**: Floating above the feed.
- **Styling**: Pure white background, subtle border, shadow-lg on focus.
- **Buttons**: Secondary actions (mode toggle, model switch) use neutral tonal buttons.

## 4. Interactions & Animations
- **Entrance**: Stream items should slide up 12px and fade in over 400ms.
- **Feedback**: Buttons use a subtle scale-down on click (0.98x).
- **Streaming**: Text should appear with a smooth opacity ramp, avoiding "jumpy" layouts.

## 5. Layout Structure
- **Sidebar Width**: 280px (Collapsible on mobile).
- **Main Feed**: Centered column, `max-width: 860px`.
- **Gutter**: 24px-48px padding for breathing room.

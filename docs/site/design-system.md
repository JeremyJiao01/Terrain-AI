# CodeGraphWiki - Product Website Design System

> Status: Active
> Last Updated: 2026-04-05
> Inspiration: [Trifecta](https://www.trifecta.xyz/) & SoulRich 2.0

This document outlines the current design specifications, color palette, typography, and interactive elements used for the CodeGraphWiki landing page (`docs/site/`). It reflects a transition from a cold, purely functional aesthetic to a **lively, elegant, and artistic** "Light" theme.

## 1. Design Philosophy

The aesthetic combines **Dieter Rams' minimalist principles** with a **vibrant, artistic editorial style**. The goal is to feel technical and precise while simultaneously appearing modern, energetic, and highly crafted.

**Key Traits:**
- **Elegant Lightness:** A warm off-white background (`#FAF9F6`) prevents the harshness of pure white while maintaining a clean canvas.
- **Artistic Typography:** A strong contrast between highly readable sans-serif body text and a dramatic, elegant serif display font with italic emphasis.
- **Vibrant Aura:** A dynamic, slow-breathing mesh gradient background adds life and movement without cluttering the content.
- **Physical Texture:** A subtle, static SVG noise/grain overlay (4% opacity) gives the digital space a tactile, paper-like quality.

## 2. Color Palette

The color system follows a modified **60-30-10 rule**, emphasizing lively, high-saturation accents over a neutral canvas.

### 2.1 Base & Neutral Colors (60%)
- **Background Neutral:** `var(--color-bg-neutral)` — `#FAF9F6` (Very warm, clean white. The primary canvas).
- **Background White:** `var(--color-bg-white)` — `#FFFFFF` (Used for elevated cards and terminal windows).
- **Material 1 (Lightest):** `var(--color-material-1)` — `#F0EFEA` (Used for code block backgrounds and subtle hovered states).
- **Material 2 (Borders):** `var(--color-material-2)` — `#E6E5DF` (Used for structural lines and card borders).
- **Material 3 (Muted):** `var(--color-material-3)` — `#C2C2BB` (Used for disabled or highly muted elements).

### 2.2 Text Colors (30%)
- **Primary Text:** `var(--color-text-primary)` — `#111111` (Near black for maximum readability and high contrast on headings).
- **Secondary Text:** `var(--color-text-secondary)` — `#555555` (Used for body paragraphs and subtitles).
- **Tertiary Text:** `var(--color-text-tertiary)` — `#888888` (Used for metadata, small labels, and subtle UI hints).

### 2.3 Vibrant Accents (10%)
These high-saturation colors are used sparingly for interactivity, progress bars, and the dynamic background aura.

- **Coral Orange:** `var(--color-accent-orange)` — `#FF5C39` (Primary accent. Used for the scroll progress bar, pain-point borders, and the first background glow).
- **Royal Blue:** `var(--color-accent-blue)` — `#2B5AFA` (Used for terminal prompts, active selected states, and the second background glow).
- **Mint Green:** `var(--color-accent-green)` — `#00C48C` (Used for success states, completed pipeline steps, and the third background glow).
- **Pink:** `var(--color-accent-pink)` — `#FF3366` (Available for future highlight usage).

## 3. Typography

The typography leverages Google Fonts to create an editorial, artistic vibe.

- **Display Font (`var(--font-display)`):** **[Instrument Serif](https://fonts.google.com/specimen/Instrument+Serif)**
  - **Usage:** Main headings (`H1`, `H2`) and large pull quotes (e.g., the Pain Points section).
  - **Treatment:** Used predominantly in `font-weight: 400` with liberal use of *italics* (`font-style: italic`) for emphasis and artistic flair.
- **Primary Font (`var(--font-primary)`):** **[Inter](https://fonts.google.com/specimen/Inter)** (Fallback: "Helvetica Neue", sans-serif)
  - **Usage:** Body text, subtitles, UI elements, buttons.
  - **Treatment:** Used in regular (400), medium (500), and semibold (600) weights to establish clear information hierarchy.
- **Monospace Font:** `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`
  - **Usage:** Code blocks, terminal windows, tree-view nodes.

## 4. Layout & Spacing

- **Container:** An elegant, narrow reading column (`max-width: 800px`) centered on the page. This prevents eye strain and feels more like an editorial layout than a sprawling dashboard.
- **Spacing Unit:** Based on an 8px grid.
  - `sm`: 8px, `md`: 16px, `lg`: 24px, `xl`: 32px, `xxl`: 48px, `xxxl`: 64px
- **Section Gap:** `160px` (`var(--section-gap)`) provides generous vertical breathing room between major sections.

## 5. Interactive & Dynamic Elements

- **Dynamic Background (Aura + Grain):**
  - A fixed `100vw x 100vh` background layer sitting at `z-index: -1`.
  - **Aura:** Three absolutely positioned `div` elements (using Coral, Royal Blue, and Mint) with `100px` blur and `25%` opacity. They animate via `@keyframes float-bg` over a 20-25 second cycle, scaling and translating to simulate slow breathing.
  - **Grain:** A static SVG `<feTurbulence>` filter overlay at `4%` opacity adds physical texture and prevents color banding.
- **Scroll Reveal (`.reveal`):**
  - Elements fade in and translate upward (`translateY(40px)` -> `0`) over `0.8s` using a smooth easing curve (`cubic-bezier(0.16, 1, 0.3, 1)`) as they enter the viewport.
- **Terminal Window:**
  - Designed to look like a modern macOS terminal.
  - Features a light-theme aesthetic (`#FFFFFF` background) with a subtle shadow (`0 20px 40px rgba(0,0,0,0.06)`).
  - Native-looking close/minimize/maximize traffic light buttons.
- **Cards & Inspector:**
  - Flat design with subtle `1px solid var(--color-material-2)` borders and very soft, wide drop shadows to lift them slightly off the textured background.
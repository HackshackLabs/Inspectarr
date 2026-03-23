# UI review and implementation backlog

This document records a design review of **Insecpectarr** against the principles and deliverables described in **[`UIDESIGN.md`](../UIDESIGN.md)** (design-system-first approach, WCAG AA, responsive framework, component consistency, and developer handoff patterns). Use it to prioritize UI work; update sections as items ship.

## Recently implemented (2026-03)

- **P0 (partial):** Skip link (`#main-content`), `<nav aria-label="Primary">`, `aria-current="page"` via `nav_current` context, descriptive logo `alt`, `:focus-visible` rings on interactive controls, Library Unwatched **`<dialog>`** for confirm/alert (with `window.confirm` / `window.alert` fallback), larger Sonarr + pager touch targets (`--touch-target-min`).
- **P1 (partial):** Shared non-color **design tokens** on `body` (`--space-*`, `--radius-*`, `--font-size-*`, `--touch-target-min`, `--shadow-card`), `.table-scroll` helper, `.btn-primary` primitive, **`prefers-reduced-motion`** global shorten.
- **P2 (partial):** Active route styling for primary nav (`.nav__link--current`).

## Current implementation (snapshot)

| Area | Today |
|------|--------|
| **Styling** | Inline `<style>` in `layout.html` (tokens + themes + shared components) plus per-page `page_styles` blocks. No standalone `.css` file. |
| **Theming** | Five `body.theme-*` palettes (slate, ocean, ember, forest, paper) via CSS custom properties; chosen in **Settings**. Light mode = `paper` only. |
| **Layout** | `main` max-width **1180px** globally; **Library Unwatched** overrides to **1320px**. |
| **Typography** | System UI stack; layout defines `--font-size-small`, `--font-size-body`, `--font-size-title` tokens. |
| **Navigation** | `<nav aria-label="Primary">` with `nav__link`; active item from `nav_current` + `aria-current="page"`. |
| **Tables** | Wide data tables on History, Library Unwatched, Live activity; `.table-scroll` available for horizontal overflow; cumulative columns keep local scroll. |
| **Feedback** | Banners on Settings; Library Unwatched uses **modal `<dialog>`** for destructive confirmations and result messages (fallback to native dialogs if `HTMLDialogElement` missing). |
| **Motion** | `prefers-reduced-motion: reduce` collapses transitions/animations globally. |

## Gaps vs `UIDESIGN.md`

1. **Design tokens** — Baseline tokens live on `body` in `layout.html`; per-page styles still repeat some raw lengths—migrate incrementally to `var(--space-*)` / `var(--radius-*)`.
2. **Component library** — Buttons, inputs, and cards are still mostly redefined per template; shared `.btn-primary` is available for adoption.
3. **WCAG AA** — Focus rings and larger touch targets improved on primary flows; full contrast audit and remaining small controls not yet verified.
4. **Semantic HTML & landmarks** — Skip link, `main` id, and `<nav>` are in place; wide tables still lack `<caption>` / scroll wrappers in several views.
5. **Keyboard & assistive tech** — Tooltips rely on hover/focus-within (dashboard stream tooltips are better than pure hover-only). Library Unwatched uses **`<dialog>`** for confirmations (native fallback only); further improvements (e.g. `aria-modal` parity, return focus to invoking control) remain optional.
6. **Responsive framework** — Some breakpoints exist (dashboard viz, library cumulative columns); there is no documented breakpoint grid or container scale like `UIDESIGN.md`’s mobile-first tiers.
7. **Loading / empty / error states** — Pending snapshots use text + auto-reload; no skeleton or shared spinner pattern as described in the design deliverable template.
8. **Dark mode story** — Multiple dark themes exist; there is no “system” preference sync (`prefers-color-scheme`) unless the user picks `paper` vs dark themes manually.

---

## Prioritized improvements

### P0 — Accessibility and safety

1. ~~**Replace `alert` / `confirm` on Library Unwatched**~~ — Shipped: `<dialog>` + initial focus + Escape; `window.alert` / `window.confirm` fallback if `showModal` is missing.
2. ~~**Visible focus styles**~~ — Shipped in `layout.html` for links, buttons, `summary`, inputs.
3. ~~**Landmarks and skip link**~~ — Shipped: skip link, `#main-content`, `<nav aria-label="Primary">`.
4. ~~**Logo alt text**~~ — Shipped: `alt="{{ site_title }} logo"` when `logo_url` is set.
5. ~~**Touch targets (high-traffic controls)**~~ — Shipped for Sonarr row actions, library pager links, History apply, Settings primary / Plex buttons; widen audit elsewhere as needed.

### P1 — Design system alignment

6. ~~**Centralize tokens**~~ — Shipped baseline on `body` in `layout.html` (`--space-*`, `--radius-*`, `--font-size-*`, `--touch-target-min`, etc.).
7. **Unify interactive components** — Partial: `.btn-primary` exists; migrate History / Settings / other pages to shared classes and add `.btn--secondary` as needed.
8. **Table ergonomics** — Apply `.table-scroll` (and optional `tabindex="0"`) to wide tables; sticky `th`; optional `<caption>`.
9. ~~**`prefers-reduced-motion`**~~ — Shipped (global shorten rule).

### P2 — Polish and handoff

10. ~~**Active navigation state**~~ — Shipped: `nav_current` + `aria-current="page"` + `.nav__link--current`.
11. **Typography scale** — Optional webfont pair per `UIDESIGN.md`; tokens already document system scale.
12. **Empty states** — Dedicated empty-state blocks (icon or short message + next action) for zero-row tables where today copy is minimal.
13. **Loading pattern** — Lightweight shared “pending” component (spinner or skeleton row) for History / insights pending mode instead of text-only.
14. **Contrast QA** — Run automated checks (e.g. axe, Lighthouse) on each theme, especially `theme-ember` / `theme-forest` muted text on `card-bg`; adjust `--muted` / `--text` if any pair fails **4.5:1** for body text.
15. ~~**Documentation**~~ — README points to `layout.html` + `nav_current`; extend as components grow.

---

## Maintenance

- Re-run this review after large template additions (new insights pages, new settings sections).
- **Related docs:** [`ARCHITECTURE.md`](ARCHITECTURE.md) (routing and templates), [`CONFIGURATION.md`](CONFIGURATION.md) (theme in dashboard config).

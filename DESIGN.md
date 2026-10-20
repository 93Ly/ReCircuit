# ReCircuit design system ("Board")

Goal: look like an electronics product (a circuit board), not a generic app template.
Keep this file up to date when the design changes; the frontend and the screenshot tests follow it.

## Principles
1. **Flat and precise.** Solid colours, 1px hairline rules, 4px radius. No gradients, no drop-shadow cards, no pill buttons, no emoji.
2. **Circuit-board motif, used with restraint.** PCB traces and pads appear in the hero art, the auth side panel and the journey tracker. Nowhere else.
3. **Data looks like data.** IDs, counts, status labels and section eyebrows use the monospace stack. Prose uses the sans stack.
4. **One accent.** Green is the brand. Gold ("solder") is the only second colour: pads, the current step, chip label. Red only for errors / unrepairable.
5. **Tables, not card piles.** Device lists are ruled rows (device, details, status, action). They collapse to stacked rows on mobile.
6. **Real facts only.** Landing-page numbers come from the UN Global E-waste Monitor 2024 (62 Mt generated in 2022, 22.3% documented as properly recycled, 82 Mt projected for 2030) and link to the source. Never invent user or impact statistics.

## Tokens
| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` | `#f2f3ee` | `#0b1510` | page background |
| `--panel` | `#ffffff` | `#111e17` | rows, forms, modal |
| `--ink` | `#0f1f17` | `#e7efe9` | text |
| `--muted` | `#56655c` | `#8ea396` | secondary text |
| `--rule` | `#d5dbd2` | `#24352b` | hairlines, borders |
| `--board` | `#0e2a1e` | `#0e2a1e` | hero / auth side panel (solder-mask green) |
| `--trace` | `#3fae77` | `#3fae77` | PCB traces, done steps |
| `--action` | `#1d6b43` | `#3fae77` | primary buttons, links |
| `--gold` | `#c9962e` | `#d8a840` | pads, current step, chip label |
| `--danger` | `#a8321f` | `#e0705c` | errors, unrepairable |

Type: sans = Segoe UI / system-ui; mono = ui-monospace / Cascadia Mono / Consolas. No external fonts (works offline, no CDN dependency).
Headings: weight 700, letter-spacing -0.02em. Eyebrows: mono, 12px, uppercase, letter-spacing .08em.

## Components
- **Status tag**: mono uppercase, 1px border, square corners, leading dot. Dot colour by phase: waiting = gold, in progress = green, finished = ink, problem = red.
- **Journey tracker**: horizontal trace with pads. Done = filled green pad, current = gold ring, upcoming = hollow pad.
- **Buttons**: rectangular, 4px radius, 40px min height, visible `:focus-visible` outline. Primary = solid action colour. Secondary = 1px outline.
- **Modal**: `role="dialog"`, closes on Escape / backdrop, first field focused.
- **Toast**: bottom-left, dark panel with a green left bar, `aria-live="polite"`.
- **View tabs** (My devices / Reuse marketplace): text tabs, the selected one underlined in gold, no boxes.
- **Filter bar**: labelled selects in a row above a table, no card around them.
- **Triage box**: a tinted block with a gold left rule showing the recommendation and its reason. In table rows it is one small mono line ("TRIAGE: REPAIR (chosen: RECYCLE)").
- **Failure note**: red text under the details, always with the reason and the attempt count.
- **Slot picker**: a plain select inside a modal; options read "Tue, Jan 6, 09:00 to 12:00 (2 left)".

## Layout rules
- Max content width 1040px. 16px side gutters on mobile. No horizontal scroll at 360px width (tested).
- Respect `prefers-color-scheme` and `prefers-reduced-motion` (trace draw animation is disabled).

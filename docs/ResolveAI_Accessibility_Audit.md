# ResolveAI Frontend Accessibility Audit (Issue #20)

Full a11y pass across all seven Module I screens: Login, Risk Command
Center, Dispute Queue, Case Workspace, Response Review, Model Metrics,
Audit Activity. Scoped to WCAG 2.1 A/AA, per the issue's own "WCAG AA at
minimum" ask.

## Method

1. **Automated**: `frontend/e2e/accessibility.spec.ts` runs axe-core
   (`@axe-core/playwright`, tags `wcag2a`/`wcag2aa`/`wcag21aa`) against
   all seven screens through a real signed-in session with real seeded
   backend data (not static markup), so conditionally-rendered states are
   covered, not just a component's default render. Runs as part of
   `npm run test:e2e` / CI (`docker-build.yml`) alongside the rest of the
   E2E suite -- a future regression here fails the build, not just this
   one-time audit.
2. **Manual**: axe-core cannot reliably compute color contrast through a
   CSS background gradient or `backdrop-filter` (it reports those as
   `incomplete`, not pass/fail -- confirmed directly: every navbar link
   came back `incomplete` on `color-contrast`, not `pass`, because of
   `body`'s radial-gradient background and `.navbar`'s semi-transparent
   blur). Checked every design-token color pair by hand (relative
   luminance / WCAG contrast formula) instead of trusting the automated
   pass as complete coverage. Also traced full keyboard-only navigation
   through the core reviewer flow (queue -> case workspace -> response
   review -> submit), since axe-core doesn't verify tab order or
   keyboard operability either.

## Findings

### Fixed

- **`--text-faint` contrast failure (real, confirmed by hand).**
  `#5b6577` measured ~3.3:1 against this theme's dark backgrounds --
  clears WCAG's 3:1 *large-text* floor but not the 4.5:1 *normal-text*
  floor that actually applies here. Used on two real, non-decorative
  text nodes: `.nav-link-muted` ("Switch identity", 13.5px/500 --
  rendered on every single authenticated page's navbar) and `.kpi-sub`
  (Risk Command Center's KPI card subtext, 12px -- the first screen
  after sign-in). Axe-core structurally cannot catch this (see above).
  Changed to `#79818f` (~5.0-5.2:1 against both the flat page background
  and the navbar's worst-case composited background), preserving the
  token's role as the dimmest of the three text tiers
  (`--text` > `--text-muted` > `--text-faint`) while actually clearing
  AA. Single design-token change (`src/index.css`); no component changed.
- **Missing table header `scope` attributes.** `DisputeQueue.tsx`'s
  table headers had none (`ModelMetrics.tsx`'s confusion-matrix table
  already had `scope="col"`/`scope="row"` correctly -- this just brings
  the other data table in line). Axe's WCAG-tagged ruleset doesn't fail
  this as a hard AA violation for a table this simple, but it's a
  correct, cheap fix with no downside. Also gave the trailing empty
  action-column header a `.visually-hidden` "Actions" label instead of
  leaving a header cell with no accessible name at all.

### Verified, not changed (no defect found)

- **Every badge/button/link color pair** (`badge-indigo/success/warning
  /danger/gray`, `.btn-primary/success/danger/warning`, `--text`,
  `--text-muted`, `--primary-light` links): computed by hand against
  both the flat page background and realistic blended backgrounds
  (badge "dim" backgrounds, the navbar's gradient+blur composite).
  All clear 4.5:1, several with comfortable margin (6-9:1). This
  includes `badge-danger`'s text color (the guardrail "FAIL" state),
  which the automated run never got to render since it depends on an
  actually-generated draft the audit didn't trigger (generating one is
  a real mutating action with real side effects, not something to do
  from an audit).
- **Keyboard-only operability of the full reviewer flow.** Traced every
  Tab stop from Dispute Queue through Case Workspace into Response
  Review's action form (role select -> override-reason input -> notes
  textarea -> submit): every interactive element reachable in a
  sensible order matching visual layout, no keyboard trap, activating a
  focused link via Enter works identically to a click. `RoleGate`
  (gates the review-action form to the APPROVER role) is a true
  conditional render -- content a role can't use is removed from the
  DOM entirely, not just visually hidden, so it never pollutes tab
  order or gets announced to a screen reader as present-but-useless.
- **Focus visibility.** Every interactive element (links, buttons,
  inputs, selects) shows a clearly visible focus ring on real
  keyboard-triggered focus. `.form-input`/`.form-select`/`.form-textarea`
  intentionally replace the native `outline` with an equivalent
  `box-shadow` + border-color change (the only `outline: none` in the
  stylesheet) -- confirmed by hand that it actually renders once its
  `0.2s` transition settles; reading computed style synchronously
  mid-transition is misleading and produced a false alarm during this
  audit before the fix was understood to be a test-timing artifact, not
  a real gap. Links and buttons keep the browser's native focus ring
  (no stylesheet rule removes it for them).
- **Existing semantic foundations were already solid**: `AsyncState`
  (shared loading/empty/error wrapper used by every screen) already
  uses `role="status"` for loading/empty and `role="alert"` for errors,
  with `aria-hidden="true"` on every purely-decorative icon (`◇` empty
  state, spinner). Form fields already use proper `<label htmlFor>`
  associations. No color-only status indicators found -- every badge
  pairs its color with visible text (`PENDING`, `CONTEST`, `FAIL`, etc.).

## Out of scope

- A full visual/manual screen-reader pass (NVDA/VoiceOver) wasn't run --
  axe-core plus the manual contrast/keyboard checks above cover what
  this audit's issue asked for; a real screen-reader pass is a
  reasonable future follow-up if deeper coverage is wanted.
- `badge-danger`'s rendered-in-context contrast (the "FAIL" guardrail
  state) was verified by direct color-math, not by actually triggering
  it in the browser, for the reason given above.

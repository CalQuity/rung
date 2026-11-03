# Writing a feature map

A feature map says, from a user's point of view, what the app does, how a user reaches each
feature, how a script drives it, and what observable state proves it works. A person reads it and
approves each feature before any drive script is written for it; the drive script then implements
the approved recipes, one scenario per sub-feature. Write it for a reader who has never seen the
app: an agent, mid-task, or a reviewer deciding whether the map describes the product they know.

Answer everything from the code (routes, commands, templates, menus, docs). Do not invent features
the code does not have. When something looks unfinished (a disabled button, a route that returns
"not implemented", a menu entry with no handler), say so in that feature's `Gotchas` instead of
writing a journey through it.

## Files

`README.md`, the index, with exactly these H2 sections in this order:

1. `Baseline preconditions`: how the app is launched for verification (the command, a free port, a
   disposable data directory, seed data), and how a run tells the instance is ready and its own.
2. `Driving conventions`: the harness (Playwright for a web UI, HTTP calls for an API, a subprocess
   or PTY for a CLI), stable handles to prefer (ARIA roles and accessible names, data attributes,
   route paths, prompt strings) and that every recipe starts from the baseline state.
3. `Proof and skip reporting`: capture the user action and the resulting state, not only the final
   screen; a mutation is proven by a second, read-only view of the stored value; every artifact names
   its feature and entry point; an entry point that cannot be reached is reported with the
   precondition it lacks, never as verified through another path.
4. `Features`: one bullet per feature file, `- [Title](slug.md) covers ...`.

Then one file per user-facing feature, `<slug>.md` (lower-case kebab-case). Start with the 3 to 5
features a user relies on most. Each feature file has an H1 title, one paragraph describing the
user-visible behaviour, and exactly these four H2 sections in this order:

1. `Sub-features`: one bullet per behaviour, starting with a short unique ID in backticks:
   `` - `coupon-apply` applies a valid code and lowers the total. `` IDs are kebab-case and unique
   across the whole map; the drive script names its scenarios after them.
2. `How to get to it (user POV)`: every entry point a user has (a button, a keyboard shortcut, a
   URL, a command). A proof that drives one entry point is incomplete while the map lists others.
3. `Driving it with <harness>`: starts with `Preconditions:` and a list of the state the recipe
   needs, then labelled bullets that pair each user action with how the script performs it and the
   observable result: `- **Apply coupon.** Fill the textbox named "Coupon code" with WELCOME5 and
   choose "Apply". The total reads $32.00 and a note says the coupon was applied.`
4. `Gotchas`: traps that waste or invalidate a run (debounces, trimming, focus, data a step changes,
   anything that looks unfinished).

Keep implementation details out: no function names, no internal endpoints, no test-only hooks. Name
only user paths, stable handles, required state, actions and observable proof. Assert on the element
that carries the meaning (the total, the saved row, the message), not on every number on the page.

# Designer (UI/UX)

**Status:** implemented as a design stage (`designing`) of the HTTP runtime. No new module: the
Designer is a skill and a set of catalog entries in `src/siamang/documents/`. No schema change, no
contract change. Config gains the agent id `designer` in `[agents.*]`.

## 1. Purpose

The Designer answers: **what will the user see and do, before any code is written?** It works
after the Architect (the System Analyst's documents) and before the design gate, so the owner
approves the architecture and the user interface together, and the Programmer receives both.

```
Lead (brief) → Planner → Architect → Designer → design gate → Programmer → QA → Reviewer
```

## 2. Documents

All six are `authored`: a draft by the model, never evidence (see [HTTP_API.md](HTTP_API.md)).

| Kind | Title | Form |
|---|---|---|
| `design_tokens` | Design System / Tokens | One `json` fence: `colors` (including `text` and `background`), `typography`, `spacing`, `radius` |
| `wireframes` | Wireframe per Layar | Per screen, a heading `### <screen>` and one `html` fence: static HTML with one inline style, no script, no external URL, using the token variables |
| `ui_spec` | Spesifikasi Layar dan Komponen | Per screen: purpose, components, the empty, loading, error, and success states, phone and desktop behaviour. Starts with a table `Layar \| Route \| File` |
| `user_flow` | User Flow / Journey | A Mermaid flowchart of the main journeys, with empty, error, and cancel paths |
| `sitemap` | Sitemap dan Navigasi | A Mermaid graph of the screens and their routes |
| `accessibility` | Aksesibilitas | Contrast, keyboard use, focus, and labels |

Two model calls write them side by side, like every design stage. Parts of a stage cannot read each other, so
one call writes `design_tokens`, `wireframes`, and `ui_spec` together: the wireframes use the token names and
draw the screens the specification lists. The other writes `user_flow`, `sitemap`, and `accessibility`.
A failed part is tried again alone, with more time, and a retry on the same basis reuses what
was finished. When the work has no user interface (a library, a CLI, a backend change), the
model marks each document `not_applicable` with a reason.

## 3. What the Designer reads

The same brief as the other design stages (instruction, plan, observed project, project
specification, conversation, repository shape), plus `prior_documents`: the Planner's and the
Architect's documents, each clipped. When the repository already has an interface, the skill
tells the model to follow its framework, components, and look instead of inventing a new style.

## 4. Gate and hand-off

* Any of `planner`, `analyst`, or `designer` having a model puts the run behind the design gate.
* Without a `designer` model its documents say `not_assigned` and no `designing` phase is recorded.
* After approval, `ui_spec` and `design_tokens` are among the most binding documents in the
  Programmer's design input (`DESIGN_PRIORITY` in `documents/author.py`); `wireframes` and then `user_flow`
  follow the sequence diagram. Wireframes are large, so within the input limit they are often clipped; an edited
  one comes first.
* The owner can edit the Mermaid diagrams, each wireframe's HTML, and the token JSON, in the same three modes as
  every design diagram: at the gate, after a stopped run, or as a revision task once the work is published
  ([HTTP_API.md](HTTP_API.md#owner-edits-to-the-design)). A revision's freshly written design takes the owner's block
  back in its place when a block of the same language is there, and adds it at the end otherwise
  (`carry_block` in `documents/diagrams.py`).

## 5. How the web interface draws them

* **Tokens** (`design_tokens`): colors as swatches, the rest as CSS variables. Each token becomes `--color-<name>`,
  `--font-<name>`, `--space-<name>`, or `--radius-<name>`, nested keys joined with a hyphen; a number in spacing
  or radius gets `px`. A value with braces, a semicolon, angle brackets, a backslash, or a comment is dropped.
* **Wireframes**: each `html` block in an `<iframe sandbox="">` (no script, no forms, an opaque origin) whose page
  carries the policy `default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:`, so it loads
  nothing from anywhere. It is drawn at 1280 px or 390 px and scaled to fit. The token variables are on `:root`.
* An `html` or `json` fence in any other document stays plain code.

## 6. Not yet

What comes next, such as re-running only the work packages a design change touches and checking contrast, is in
[VISION.md](VISION.md#desainer-uiux).

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

All five are `authored`: a draft by the model, never evidence (see [HTTP_API.md](HTTP_API.md)).

| Kind | Title | Form |
|---|---|---|
| `user_flow` | User Flow / Journey | A Mermaid flowchart of the main journeys, with empty, error, and cancel paths |
| `sitemap` | Sitemap dan Navigasi | A Mermaid graph of the screens and their routes |
| `design_tokens` | Design System / Tokens | One `json` fence: `colors` (including `text` and `background`), `typography`, `spacing`, `radius` |
| `ui_spec` | Spesifikasi Layar dan Komponen | Per screen: purpose, components, the empty, loading, error, and success states, phone and desktop behaviour. Starts with a table `Layar \| Route \| File` |
| `accessibility` | Aksesibilitas | Contrast, keyboard use, focus, and labels |

Two model calls write them side by side (three documents, then two), like every design stage.
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
  Programmer's design input (`DESIGN_PRIORITY` in `documents/author.py`); `user_flow` follows the
  sequence diagram.
* The owner can edit the Mermaid diagrams of `user_flow` and `sitemap` with the diagram editor,
  in the same three modes as every design diagram: at the gate, after a stopped run, or as a
  revision task once the work is published.

## 5. Not yet

What comes next, such as HTML wireframes, editing tokens, and re-running only the work packages
a design change touches, is in [VISION.md](VISION.md#desainer-uiux).

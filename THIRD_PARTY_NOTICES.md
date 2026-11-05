# Third-party notices

The MIT license of this project covers its own code. Dependencies keep their own licenses and are installed by pip or npm, not stored in this repository. The Python core uses only the standard library; FastAPI and uvicorn are optional. The packages below are for the interface in `web/`.

| Package | License | Used for |
|---|---|---|
| [Vue](https://github.com/vuejs/core), [Vue Router](https://github.com/vuejs/router), [Pinia](https://github.com/vuejs/pinia) | MIT | Interface |
| [Three.js](https://github.com/mrdoob/three.js) | MIT | The 3D office |
| [Lucide](https://github.com/lucide-icons/lucide) (`@lucide/vue`) | ISC | Icons |
| [Mermaid](https://github.com/mermaid-js/mermaid) | MIT | Diagrams in documents |

Mermaid brings its own dependencies, which end up in the built `dist/`. All are permissive except two to note if you redistribute a build:

- [elkjs](https://github.com/kieler/elkjs): Eclipse Public License 2.0.
- [DOMPurify](https://github.com/cure53/DOMPurify): MPL 2.0 or Apache 2.0, at your choice.

To list every license of what you install, run this in `web/`: `npx license-checker --production --summary`.

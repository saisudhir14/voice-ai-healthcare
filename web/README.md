# Village Line UI

Angular 22 + Angular Material. Standalone components, zoneless change detection,
signal state, strict template checking and a custom SCSS theme.

Run `npm ci && npm run dev` and open http://localhost:8173.
The API proxy targets port 7860 (`API_PORT` overrides it).
Use `?mock=1` for the offline demo; `&speed=100` accelerates the scripted replay.

- `src/app/`: workspace service, Angular components, templates and styles.
- `src/lib/`: event reducer, typed API/SSE adapters, voice, demo and chart normalizers.
- `src/styles.scss`: Material theme, design tokens and shared chart styles.
- `serve.mjs`: preserves the root `dev.sh` host/port contract.

Validation:

```sh
npm run typecheck
npm test -- --watch=false
npm run build
npx playwright install firefox
npm run test:e2e
```

E2E uses Firefox only on port 8174. Production assets are in
`dist/village-line/browser/`. See the repository README for phone setup and voice API startup.

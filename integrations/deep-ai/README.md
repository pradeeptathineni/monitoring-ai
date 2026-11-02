# Deep AI consumer

`client.ts` is a server-side, dependency-free TypeScript consumption adapter. It is delivered here for the Deep implementation owner to review and apply. This repository does not modify or claim deployment into Deep.

```ts
import { monitoringOwner } from "./client";

// Operator configuration on the Deep host, never a browser-supplied URL
const monitoring = monitoringOwner("http://127.0.0.1:5274");
const snapshot = await monitoring.snapshot({ project: "orbit-api" });
```

Deep should expose a same-origin read bridge to its browser. Use capabilities to check compatibility before showing a connected state. Poll snapshots every two seconds, stop polling when the view is closed, and retain last observations with a visible unavailable label on failure. Reconnect explicitly after the owner identity changes. No mutation, cancellation, execution or session reopening endpoint exists in this adapter.

Render `reported_state`, `state`, `stale`, `status_at` and `source_status` with their distinct meanings. A successful HTTP read does not refresh worker evidence. Cost amounts cover the indicated event count. Keep estimated, billed, unknown and unconverted subsets visible. Show the owner's supplied currency and FX provenance.

Deep's existing Signals activity, Maestro managed runs and native Codex session roster retain their own authoritative controls and exact IDs. Monitoring's source/session/entity tuple identifies an observation, not a command target. Do not route cancellation or opening actions from a monitoring row without an independently verified owner mapping. Do not add provider totals together when their underlying usage overlaps. See [the owner contract](../../docs/contract.md).

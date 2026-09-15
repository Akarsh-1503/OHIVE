# ADR-0007 — Flat typed arrays and gzip, over object-per-point JSON

**Status:** accepted · **Date:** 2026-09 · **Code:** `slam/results.py`, `backend/app/exports.py`, `frontend/src/lib/recon.ts`

## Context

The reconstruction payload is the largest thing this API returns and the only one on the
critical path of the user's experience: it is fetched the instant a job completes and must be
on screen as a 3D scene immediately after. Up to **60 000 points** plus one pose per processed
frame.

The obvious JSON shape is one object per point:

```jsonc
{"points": [{"x": 1.23, "y": -4.56, "z": 7.89, "r": 33, "g": 31, "b": 32, "obs": 5}, …]}
```

It is readable, self-describing, and the wrong choice here.

## Options

### A. Object per point

At 60 000 points: roughly 60 000 JSON objects, each with seven keys. The **keys repeat 60 000
times** — `"x"`, `"y"`, `"z"`, `"r"`, `"g"`, `"b"`, `"obs"` is ~30 bytes of field names per
point, ~1.8 MB of pure redundancy before any values.

Worse than the bytes: `JSON.parse` materialises **60 000 short-lived JavaScript objects** that
are then walked to fill a `Float32Array` and immediately discarded. That is main-thread work
and GC pressure at exactly the moment the user is waiting for the scene.

### B. Binary — Protobuf, MessagePack, glTF, a custom format

Smallest on the wire and fastest to parse. Costs: a schema and a code generator on both sides,
an unreadable payload in browser devtools, `curl | jq` stops working, and the frozen API
contract would have to describe a binary layout. For a payload that gzips to ~122 kB, that is
a lot of machinery.

### C. Flat typed arrays inside JSON, gzipped at write time · **chosen**

```jsonc
"points": {
  "xyz": [4.0328, 3.9374, 25.3004, 2.2679, 3.9041, 25.377, …],  // float32, flattened
  "rgb": [33, 31, 32, 34, 35, 30, …],                           // uint8, flattened
  "observations": [3, 5, 5, 5, 5, 4, …]
}
```

Three keys total, not three per point. Still JSON — inspectable in devtools, greppable,
`jq`-able, describable in a text contract.

## Decision

**C.** This is the shape frozen into
[`_contracts/assignment2-api.md`](../../../_contracts/assignment2-api.md).

## Consequences

### Measured, on `synthetic_loop` (3434 points, 300 poses)

| | |
|---|---|
| `reconstruction.json` on disk, uncompressed | **336 kB** |
| `web.json.gz` on disk, pre-gzipped | **122 kB** |
| Wire response, client without `Accept-Encoding: gzip` | 316 kB |
| PLY export, full cloud, binary | 52 kB |

Flat float arrays gzip well — mantissa digits vary but the structural noise is gone — and the
observation array is a small-integer run that gzip compresses hard.

### Gzipped once, at write time

The payload is compressed when the worker writes it and served pre-compressed. There is
deliberately **no `GZipMiddleware`**: blanket middleware would buffer whole sample videos into
memory and burn CPU re-compressing already-compressed JPEG previews, to save nothing on the
one response that matters because it is already compressed.

### Consumed without allocating a single object

`frontend/src/lib/recon.ts`, the only place the payload is touched, states the rule in its
header:

> Nothing here ever allocates a per-point JavaScript object: a 60 000-point payload becomes
> four typed arrays and is handed straight to a single `THREE.Points`.

`Float32Array.from(points.xyz)` is one pass with no intermediate objects, and the result is a
`bufferGeometry` attribute — exactly the layout the GPU wants, with no repacking.

The result is a single draw call for the entire cloud and **1.65 ms/frame at 50 000 points**
on an M4 ([05 — Frontend](../05-frontend.md)), roughly 10× headroom at 60 Hz.

### The point budget, and why the subsample is not random

Points are capped at `SLAM_MAX_POINTS_WEB` (60 000). The subsample is **deterministic and
quality-aware**: ranked by observation count descending, ties broken by per-point reprojection
error ascending, with the **last 15 % of the budget filled by a uniform stride** over what
remains.

That last clause exists because a pure top-N silently deletes whole regions of the map — the
ones only a handful of keyframes ever saw, which is typically the *end of the trajectory*. A
reviewer would see a cloud that mysteriously stops. The stride preserves spatial coverage at a
small cost in average point quality.

No RNG is involved, so the same reconstruction always produces the same web payload.

### Point order is shuffled — once, in the browser

A seeded shuffle at scene-build time means the viewer's density slider is `setDrawRange(0, n)`
rather than a re-upload. Without it, drawing the first *n* points would lop off whole spatial
regions, because the map is written roughly in trajectory order.

### The full cloud is never lost

The web cap is a *browser* budget, not a data limit. `GET /jobs/{id}/export/ply` always serves
the complete cloud as binary little-endian PLY, ready for MeshLab or CloudCompare.

### What this costs

- **Not self-describing.** `[4.0328, 3.9374, 25.3004, …]` means nothing without the contract
  saying it is xyz-major. Mitigated by the contract, by the field names (`xyz`, not `data`),
  and by the PLY export carrying a real header.
- **Index arithmetic on both sides.** Point `i` is `xyz[3i : 3i+3]`. Confined to two files —
  `slam/results.py` and `frontend/src/lib/recon.ts` — and nothing else in either codebase does
  it.
- **Still not as small as binary.** A Protobuf encoding would be perhaps 40 % of the gzipped
  size. At 122 kB that saves ~70 kB, which is not worth a schema compiler.

# Take-home submissions

Two independent projects, each on its own branch.

| branch | project | what it is |
|---|---|---|
| `assignment-1-leadforge` | **LeadForge** | Bulk business-card → structured-lead extraction, powered by a self-hosted Qwen2.5-VL vision-language model |
| `assignment-2-driftless` | **Driftless** | Monocular RGB sparse point-cloud SLAM: video in, 3D map and camera trajectory out |

Each branch carries its own `README.md`, `docs/` (architecture, decisions, ADRs, measured
performance, limitations) and a `docker compose up` quickstart that runs with no credentials.

```bash
git checkout assignment-1-leadforge
git checkout assignment-2-driftless
```

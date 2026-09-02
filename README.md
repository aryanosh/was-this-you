# Face → Reverse Search → Blockchain Verification Pipeline

Built for HH Goa 2026 Task 3. Status: **work in progress, building stage by stage.**

This section will be filled in fully once the pipeline is complete end-to-end
(see the project plan for the stage list). For now:

## Setup (in progress)

Backend requires **Python 3.11** specifically (not the system default) because
`dlib`/`face_recognition` prebuilt wheels for Windows only exist up to 3.11-3.12.

```
py -3.11 -m venv backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt
```

Copy `.env.example` to `.env` and fill in `SERPAPI_KEY` before Stage 2.

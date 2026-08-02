# docs/ui_baseline/

The M14 plan (P0.2) lists 23 reference screenshots. They could not be captured in the execution
environment: every state worth photographing (streaming reply, history rows, reminders, search
results, camera panel, fired toast) requires the FastAPI backend, and the environment had no
uvicorn and no network.

What replaces them is recorded in `../UI_BASELINE.md` section 0 and section 3: computed-style
fingerprints taken through headless Chromium against a static file server. Where a phase could not
be verified by that method either, the phase report says so explicitly instead of claiming a pass.

If you later run the app locally, capture the 23 states listed in the plan into this folder - the
fingerprints will still be the machine-checkable gate, and the screenshots become the human one.

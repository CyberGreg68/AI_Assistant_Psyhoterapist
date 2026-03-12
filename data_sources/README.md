# Upstream Snapshot Contracts

This directory is reserved for exports from the external system-of-record used for profile sync.

The default `json_snapshot` provider expects these files:

- `patients.snapshot.json`
- `clinicians.snapshot.json`
- `assistants.snapshot.json`
- `assignments.snapshot.json`
- `patient_history.snapshot.json`

These files are not the runtime source of truth. They are input snapshots that `scripts/sync_profile_registry.py` transforms into `config/profile_registry.generated.jsonc`.

Recommended practice:

1. Export snapshots from the upstream EHR, CRM, or scheduling platform on a schedule.
2. Copy only the minimum routing and consent fields needed by the assistant runtime.
3. Run `python scripts/sync_profile_registry.py` before deployment or on a controlled refresh cadence.
4. Keep sensitive historical content summarized rather than copying full notes into the runtime layer.
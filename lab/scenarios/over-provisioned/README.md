# Scenario: over-provisioned (efficiency / cost)

- **Waste:** `web` reserves `cpu: 500m / mem: 256Mi` (limits `1 / 512Mi`) but an idle nginx uses
  ~1m CPU / ~6Mi → CPU utilization well under 2%.
- **Detection:** `kubectl top` usage ≪ requests → efficiency issue `over-provisioned`.
- **Recommendation:** **RIGHT_SIZE_DOWN** — set requests ≈ usage×safety (floors 10m/16Mi) →
  large **cost-units** reduction.
- **Validation:** pods Ready + cost-units drop.
- **Reset:** re-apply base (requests 25m/32Mi).

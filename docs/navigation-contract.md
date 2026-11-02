# Shared navigation adoption

Monitoring adapts the shared UI owner's navigation pattern at `93f5754fb4c92775381c103c5ef212de3cf5ed96`. The MIT stylesheet is bundled locally with its notice. Its declarations remain unchanged: only the workspace selector prefix and an attribution header differ. [Provenance and hashes](navigation-source.json) record the exact source and transformation. There is no new navigation framework or running Deep dependency.

The shared geometry uses a 236px expanded desktop rail, a 68px icon rail, a 208px contextual sidebar (184px at intermediate widths), and horizontal navigation below 861px. Native controls keep accessible names and title tooltips. Current destinations remain visible in the scrollable mobile strips. The local adapter preserves Monitoring's layout, status and accounting meanings.

Settings supplies actual owner categories through the contextual navigation slot:

| Route | Meaning |
| --- | --- |
| `/#settings` | All Settings, preserving the original destination |
| `/#settings/appearance` | Existing appearance preferences |
| `/#settings/sources` | Explicit source setup and ingestion-readiness guidance |

The original Overview, Sessions, Usage, Events and Report hashes and shortcuts remain. Settings retains shortcut 6. Contextual navigation is absent from monitoring views that already have clear filters or drill-downs. Appearance remains outside Overview.

The rail's last choice is local to this Monitoring origin under `monitoring-ai.navigation.collapsed.v1`. It does not migrate or change `monitoring-ai.appearance.v1`, connect sources, launch work or grant access. Blocked storage leaves layout controls usable for the current page. Each standalone owns its preference storage; no cross-application layout synchronization is introduced.

Motion follows the shared restrained overshoot curve and disclosure entrance. Browsers with intrinsic-size interpolation can animate disclosure sizing. Reduced-motion preference disables these effects. Native controls and their focus remain usable without animation. This is a bounded browser adaptation, not a claim of every browser or assistive-technology combination being qualified.

# Compare recorded sessions

Open Usage report, choose two distinct source/session pairs under Compare recorded sessions, then choose Compare sessions. Choices come from entities visible in the current snapshot, which is limited to 500 entities. Clear restrictive filters if the session you need is outside that selection.

Provider, model, surface, project and UTC date filters apply to both reports. The two chosen pairs replace the shared source/session filters. Current state is omitted so historical records remain included. Each comparison reads the existing report endpoint twice and displays results only when both reports share an owner identity, ledger revision and currency. A ledger change between reads requires another explicit comparison.

The table shows recorded usage, disjoint token buckets, tool outcomes and cost coverage. Count differences are second minus first. Estimated and billed costs remain separate with their covered record counts. Unknown and unconverted records stay visible. Cost differences are omitted because coverage can differ. Receipt-time summaries can describe earlier work, and missing sources can leave either session incomplete. Counts do not measure task quality or prove current activity.

Comparisons refresh only when requested. A filter, owner or session selection change clears the comparison. A later snapshot revision marks retained results as older. Leaving the report or pausing refresh cancels an in-flight comparison. This adds no new server route, provider access or Deep UI integration.

#!/usr/bin/env bash
# assert-idempotent.sh — parse az deployment group what-if JSON output and
# assert that every change entry is NoChange or Ignore.
#
# Usage:
#   az deployment group what-if ... --result-format FullResourcePayloads \
#       --output json | ./scripts/assert-idempotent.sh
#
# Exit codes:
#   0  — all changes are NoChange / Ignore  (idempotent)
#   1  — one or more resource modifications detected (not idempotent)
#
# The script is intentionally lenient about what counts as "idempotent" input:
# - An empty JSON array [] is fine (nothing would change)
# - changeType "NoChange" is fine
# - changeType "Ignore" is fine (unsupported resource types — ARM skips these)
# - Any other changeType (Create, Modify, Delete, Deploy, Unsupported) is a failure
#
# Design note: we parse the raw --output json form, not the human-readable
# table, because the text output changes wording between az CLI versions and
# is fragile to parse.  The JSON schema is versioned and stable.

set -euo pipefail

WHAT_IF_JSON="${1:-}"

if [[ -n "$WHAT_IF_JSON" ]]; then
  INPUT="$(cat "$WHAT_IF_JSON")"
else
  INPUT="$(cat)"
fi

# The what-if JSON is either:
#   { "changes": [...] }           (wrapped in a result object)
#   [...]                          (bare array — older az CLI versions)
# Normalise to an array.
changes="$(echo "$INPUT" | jq 'if type == "object" then .changes else . end')"

total="$(echo "$changes" | jq 'length')"

if [[ "$total" -eq 0 ]]; then
  echo "✅  Idempotency check PASSED: what-if returned no changes (empty change set)."
  exit 0
fi

# Filter down to actionable changes (anything that isn't NoChange / Ignore).
actionable="$(echo "$changes" | jq '
  [.[] | select(.changeType | IN("NoChange","Ignore") | not)]
')"

actionable_count="$(echo "$actionable" | jq 'length')"
no_change_count="$(echo "$changes"    | jq '[.[] | select(.changeType == "NoChange")] | length')"
ignore_count="$(echo "$changes"       | jq '[.[] | select(.changeType == "Ignore")]  | length')"

echo "──────────────────────────────────────────────────────────────"
echo "  Idempotency what-if summary"
echo "──────────────────────────────────────────────────────────────"
echo "  Total resources in change set : $total"
echo "  NoChange                      : $no_change_count"
echo "  Ignore (unsupported by ARM)   : $ignore_count"
echo "  Actionable changes            : $actionable_count"
echo "──────────────────────────────────────────────────────────────"

if [[ "$actionable_count" -eq 0 ]]; then
  echo "✅  Idempotency check PASSED: all $total resource(s) are NoChange or Ignore."
  exit 0
fi

echo ""
echo "❌  Idempotency check FAILED: $actionable_count resource(s) would be changed on re-deploy:"
echo ""
echo "$actionable" | jq -r '.[] | "  [\(.changeType)] \(.resourceId)"'
echo ""
echo "This means a second infra deploy would NOT be a no-op."
echo "Common causes:"
echo "  - A non-deterministic property (e.g. utcNow(), newGuid()) in a resource name or tag"
echo "  - A computed value that differs between evaluation cycles"
echo "  - A recently-changed parameter that has not yet been applied to the live RG"
echo ""
echo "Inspect the actionable resources above and ensure no Bicep expression"
echo "produces a different value on every deployment."
exit 1

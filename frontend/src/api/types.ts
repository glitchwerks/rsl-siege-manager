// Enums
export type MemberRole = "heavy_hitter" | "advanced" | "medium" | "novice";
export type SiegeStatus = "planning" | "active" | "complete";
export type BuildingType =
  | "stronghold"
  | "mana_shrine"
  | "magic_tower"
  | "defense_tower"
  | "post";

// Members
export interface Member {
  id: number;
  name: string;
  discord_username: string | null;
  discord_id?: string | null;
  role: MemberRole;
  power_level: string | null;
  is_active: boolean;
}

// Discord sync
export interface SyncMatch {
  member_id: number;
  member_name: string;
  current_discord_username: string | null;
  proposed_discord_username: string;
  proposed_discord_id: string;
  confidence: "exact" | "suggested" | "ambiguous";
}

export interface SyncPreviewResponse {
  matches: SyncMatch[];
  unmatched_guild_members: string[];
  unmatched_clan_members: string[];
}

export interface SyncApplyItem {
  member_id: number;
  discord_username: string;
  discord_id: string;
}

export interface PostCondition {
  id: number;
  description: string;
  stronghold_level: number;
}

// Siege
export interface Siege {
  id: number;
  date: string | null;
  status: SiegeStatus;
  defense_scroll_count: number;
  computed_scroll_count: number;
  created_at: string;
  updated_at: string;
}

// Building
export interface Building {
  id: number;
  siege_id: number;
  building_type: BuildingType;
  building_number: number;
  level: number;
  is_broken: boolean;
}

// Member preferences bulk summary
export interface MemberPreferenceSummary {
  member_id: number;
  member_name: string;
  preferences: PostCondition[];
}

// Board types (nested hierarchy from GET /api/sieges/{id}/board)
export interface PositionResponse {
  id: number;
  position_number: number;
  member_id: number | null;
  member_name: string | null;
  is_reserve: boolean;
  is_disabled: boolean;
  matched_condition_id: number | null;
}

export interface BuildingGroupResponse {
  id: number;
  group_number: number;
  slot_count: number;
  positions: PositionResponse[];
}

export interface BuildingResponse {
  id: number;
  building_type: BuildingType;
  building_number: number;
  level: number;
  is_broken: boolean;
  groups: BuildingGroupResponse[];
}

export interface BoardResponse {
  siege_id: number;
  buildings: BuildingResponse[];
}

// SiegeMember
export interface SiegeMember {
  siege_id: number;
  member_id: number;
  member_name: string;
  member_role: string;
  member_power_level: string | null;
  member_is_active: boolean;
  attack_day: number | null;
  has_reserve_set: boolean | null;
  attack_day_override: boolean;
}

// Post
export interface PostConditionRef {
  id: number;
  description: string;
  stronghold_level: number;
}

export interface Post {
  id: number;
  siege_id: number;
  building_id: number;
  building_number: number;
  priority: number;
  description: string | null;
  active_conditions: PostConditionRef[];
}

// Validation
export interface ValidationIssue {
  rule: number;
  message: string;
  context: Record<string, unknown> | null;
}

export interface ValidationResult {
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

// Auto-fill
export interface AutofillAssignment {
  position_id: number;
  member_id: number | null;
  is_reserve: boolean;
}

export interface AutofillPreviewResult {
  assignments: AutofillAssignment[];
  expires_at: string;
}

export interface AutofillApplyResult {
  applied_count: number;
}

// Attack day
export interface AttackDayAssignment {
  member_id: number;
  attack_day: number;
}

export interface AttackDayPreviewResult {
  assignments: AttackDayAssignment[];
  expires_at: string;
}

export interface AttackDayApplyResult {
  applied_count: number;
}

// Comparison
export interface PositionKey {
  building_type: string;
  building_number: number;
  group_number: number;
  position_number: number;
}

export interface MemberDiff {
  member_id: number;
  member_name: string;
  added: PositionKey[];
  removed: PositionKey[];
  unchanged: PositionKey[];
}

export interface ComparisonResult {
  siege_a_id: number;
  siege_b_id: number;
  members: MemberDiff[];
}

// Notifications
export interface NotificationResultItem {
  member_id: number;
  member_name: string;
  discord_username: string | null;
  success: boolean | null;
  error: string | null;
  sent_at: string | null;
}

export interface NotificationBatchResponse {
  batch_id: number;
  status: string;
  results: NotificationResultItem[];
}

export interface NotifyResponse {
  batch_id: number;
  status: string;
  member_count: number;
}

// Images
export interface GenerateImagesResponse {
  assignments_image: string; // base64
  reserves_image: string; // base64
}

// Version
export interface VersionInfo {
  backend_version: string;
  bot_version: string | null;
  frontend_version: string | null;
  git_sha: string | null;
}

// Reference
export interface BuildingTypeInfo {
  value: BuildingType;
  display: string;
  count: number;
  base_group_count: number;
  base_last_group_slots: number;
}

export interface MemberRoleInfo {
  value: MemberRole;
  display: string;
  default_attack_day: number;
}

// Post Suggestions
export type PostSuggestionSkipReason =
  | "no_conditions"
  | "no_match"
  | "reserve"
  | "disabled";

export interface PostSuggestionEntry {
  post_id: number;
  building_number: number;
  priority: number;
  position_id: number;
  suggested_member_id: number | null;
  suggested_member_name: string | null;
  suggested_condition_id: number | null;
  suggested_condition_description: string | null;
  current_member_id: number | null;
  current_member_name: string | null;
  current_condition_id: number | null;
  current_condition_description: string | null;
  matches_current: boolean;
  skip_reason: PostSuggestionSkipReason | null;
}

export interface PostSuggestionPreviewResult {
  assignments: PostSuggestionEntry[];
  expires_at: string;
}

export type PostSuggestionStaleReason =
  | "position_missing"
  | "position_disabled"
  | "position_reserve"
  | "member_inactive"
  | "member_changed"; // another planner wrote a different member since preview

export interface PostSuggestionStaleEntry {
  position_id: number;
  reason: PostSuggestionStaleReason;
}

// Apply 409 response shape: { detail: { stale_entries: PostSuggestionStaleEntry[] } }

export interface PostSuggestionApplyResult {
  applied_count: number;
}

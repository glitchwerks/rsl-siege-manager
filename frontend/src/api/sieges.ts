import apiClient from "./client";
import type {
  Siege,
  SiegeStatus,
  Building,
  BuildingType,
  BuildingTypeInfo,
  MemberPreferenceSummary,
  SiegeMember,
  ValidationResult,
  AutofillPreviewResult,
  AutofillApplyResult,
  AttackDayPreviewResult,
  AttackDayApplyResult,
  ComparisonResult,
  PostSuggestionPreviewResult,
  PostSuggestionApplyResult,
} from "./types";

export async function getSieges(params?: {
  status?: SiegeStatus;
}): Promise<Siege[]> {
  const res = await apiClient.get<Siege[]>("/api/sieges", { params });
  return res.data;
}

export async function getSiege(id: number): Promise<Siege> {
  const res = await apiClient.get<Siege>(`/api/sieges/${id}`);
  return res.data;
}

export async function createSiege(data: { date?: string }): Promise<Siege> {
  const res = await apiClient.post<Siege>("/api/sieges", data);
  return res.data;
}

export async function updateSiege(
  id: number,
  data: { date?: string | null }
): Promise<Siege> {
  const res = await apiClient.put<Siege>(`/api/sieges/${id}`, data);
  return res.data;
}

export async function deleteSiege(id: number): Promise<void> {
  await apiClient.delete(`/api/sieges/${id}`);
}

export async function activateSiege(id: number): Promise<Siege> {
  const res = await apiClient.post<Siege>(`/api/sieges/${id}/activate`);
  return res.data;
}

export async function completeSiege(id: number): Promise<Siege> {
  const res = await apiClient.post<Siege>(`/api/sieges/${id}/complete`);
  return res.data;
}

export async function cloneSiege(id: number): Promise<Siege> {
  const res = await apiClient.post<Siege>(`/api/sieges/${id}/clone`);
  return res.data;
}

export async function reopenSiege(id: number): Promise<Siege> {
  const res = await apiClient.post<Siege>(`/api/sieges/${id}/reopen`);
  return res.data;
}

export async function validateSiege(id: number): Promise<ValidationResult> {
  const res = await apiClient.post<ValidationResult>(
    `/api/sieges/${id}/validate`
  );
  return res.data;
}

export async function getBuildings(siegeId: number): Promise<Building[]> {
  const res = await apiClient.get<Building[]>(
    `/api/sieges/${siegeId}/buildings`
  );
  return res.data;
}

export async function createBuilding(
  siegeId: number,
  data: { building_type: BuildingType; building_number: number }
): Promise<Building> {
  const res = await apiClient.post<Building>(
    `/api/sieges/${siegeId}/buildings`,
    data
  );
  return res.data;
}

export async function updateBuilding(
  siegeId: number,
  buildingId: number,
  data: { level?: number; is_broken?: boolean }
): Promise<Building> {
  const res = await apiClient.put<Building>(
    `/api/sieges/${siegeId}/buildings/${buildingId}`,
    data
  );
  return res.data;
}

export async function deleteBuilding(
  siegeId: number,
  buildingId: number
): Promise<void> {
  await apiClient.delete(`/api/sieges/${siegeId}/buildings/${buildingId}`);
}

export async function getBuildingTypes(): Promise<BuildingTypeInfo[]> {
  const res = await apiClient.get<BuildingTypeInfo[]>(
    "/api/sieges/building-types"
  );
  return res.data;
}

export async function getSiegeMembers(siegeId: number): Promise<SiegeMember[]> {
  const res = await apiClient.get<SiegeMember[]>(
    `/api/sieges/${siegeId}/members`
  );
  return res.data;
}

export async function getSiegeMemberPreferences(
  siegeId: number
): Promise<MemberPreferenceSummary[]> {
  const res = await apiClient.get<MemberPreferenceSummary[]>(
    `/api/sieges/${siegeId}/members/preferences`
  );
  return res.data;
}

export async function addSiegeMember(
  siegeId: number,
  memberId: number
): Promise<SiegeMember> {
  const res = await apiClient.post<SiegeMember>(
    `/api/sieges/${siegeId}/members`,
    {
      member_id: memberId,
    }
  );
  return res.data;
}

export async function updateSiegeMember(
  siegeId: number,
  memberId: number,
  data: {
    attack_day?: number | null;
    has_reserve_set?: boolean | null;
    attack_day_override?: boolean;
  }
): Promise<SiegeMember> {
  const res = await apiClient.put<SiegeMember>(
    `/api/sieges/${siegeId}/members/${memberId}`,
    data
  );
  return res.data;
}

export async function removeSiegeMember(
  siegeId: number,
  memberId: number
): Promise<void> {
  await apiClient.delete(`/api/sieges/${siegeId}/members/${memberId}`);
}

export async function previewAutofill(
  siegeId: number
): Promise<AutofillPreviewResult> {
  const res = await apiClient.post<AutofillPreviewResult>(
    `/api/sieges/${siegeId}/auto-fill`
  );
  return res.data;
}

export async function applyAutofill(
  siegeId: number
): Promise<AutofillApplyResult> {
  const res = await apiClient.post<AutofillApplyResult>(
    `/api/sieges/${siegeId}/auto-fill/apply`
  );
  return res.data;
}

export async function previewAttackDay(
  siegeId: number
): Promise<AttackDayPreviewResult> {
  const res = await apiClient.post<AttackDayPreviewResult>(
    `/api/sieges/${siegeId}/members/auto-assign-attack-day`
  );
  return res.data;
}

export async function applyAttackDay(
  siegeId: number
): Promise<AttackDayApplyResult> {
  const res = await apiClient.post<AttackDayApplyResult>(
    `/api/sieges/${siegeId}/members/auto-assign-attack-day/apply`
  );
  return res.data;
}

export async function compareSieges(
  siegeId: number
): Promise<ComparisonResult> {
  const res = await apiClient.get<ComparisonResult>(
    `/api/sieges/${siegeId}/compare`
  );
  return res.data;
}

export async function compareSiegesSpecific(
  siegeId: number,
  otherId: number
): Promise<ComparisonResult> {
  const res = await apiClient.get<ComparisonResult>(
    `/api/sieges/${siegeId}/compare/${otherId}`
  );
  return res.data;
}

export async function previewPostSuggestions(
  siegeId: number
): Promise<PostSuggestionPreviewResult> {
  const res = await apiClient.post<PostSuggestionPreviewResult>(
    `/api/sieges/${siegeId}/post-suggestions`
  );
  return res.data;
}

export async function applyPostSuggestions(
  siegeId: number,
  applyPositionIds: number[]
): Promise<PostSuggestionApplyResult> {
  const res = await apiClient.post<PostSuggestionApplyResult>(
    `/api/sieges/${siegeId}/post-suggestions/apply`,
    { apply_position_ids: applyPositionIds }
  );
  return res.data;
}

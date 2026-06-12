from pydantic import BaseModel, model_validator

from app.schemas.post_condition import PostConditionResponse


class SiegeMemberResponse(BaseModel):
    model_config = {"from_attributes": True}
    siege_id: int
    member_id: int
    member_name: str = ""
    member_role: str = ""
    member_power_level: str | None = None
    member_is_active: bool = True
    attack_day: int | None
    has_reserve_set: bool | None
    attack_day_override: bool

    @model_validator(mode="before")
    @classmethod
    def resolve_member_fields(cls, data):
        if hasattr(data, "member") and data.member is not None:
            data.member_name = data.member.name
            data.member_role = data.member.role
            data.member_power_level = data.member.power_level
            data.member_is_active = data.member.is_active
        return data


class SiegeMemberUpdate(BaseModel):
    attack_day: int | None = None
    has_reserve_set: bool | None = None
    attack_day_override: bool | None = None


class MemberPreferenceSummary(BaseModel):
    model_config = {"from_attributes": True}
    member_id: int
    member_name: str
    preferences: list[PostConditionResponse]

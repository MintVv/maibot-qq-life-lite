from pydantic import Field, model_validator
from maibot_sdk import PluginConfigBase


class PluginSettings(PluginConfigBase):
    enabled: bool = True
    config_version: str = '1.0.0'


class Settings(PluginConfigBase):
    enabled: bool = True
    owner_ids: list[str] = Field(default_factory=list, description='可管理插件的 QQ 号')
    bot_uin: str = Field(default='', description='仅允许操作这个机器人 QQ 账号')
    adapter_id: str = 'maibot-team.snowluma-adapter'
    source_groups: list[str] = Field(default_factory=list, description='允许收藏与生成公开内容的群；空列表不读取任何群')
    mode: str = Field(default='draft', pattern='^(draft|auto)$', description='draft 只存草稿；auto 可实际发布、上传和修改资料')
    album_id: str = Field(default='', max_length=160, description='指定空间相册 ID；不以说说附件代替相册')
    auto_collect: bool = True
    auto_post: bool = True
    auto_profile: bool = False
    poll_seconds: int = Field(default=120, ge=60, le=3600)
    decision_min_minutes: int = Field(default=120, ge=20, le=1440)
    decision_max_minutes: int = Field(default=300, ge=20, le=2880)
    quiet_start: int = Field(default=0, ge=0, le=23)
    quiet_end: int = Field(default=9, ge=0, le=23)
    daily_post_limit: int = Field(default=2, ge=1, le=10)
    post_gap_minutes: int = Field(default=180, ge=10, le=10080)
    daily_upload_limit: int = Field(default=10, ge=1, le=100)
    profile_gap_hours: int = Field(default=168, ge=1, le=8760)
    max_collection: int = Field(default=500, ge=10, le=5000)
    max_image_mb: int = Field(default=8, ge=1, le=16)
    model_task: str = 'utils'
    persona: str = Field(default='你是白饭大王，蓝色小鲸鱼，嘴馋、爱摸鱼、有主见，日常说话简短机灵。不编造现实经历，不公开私聊和群友个人信息。', max_length=2000)

    @model_validator(mode='after')
    def validate_settings(self):
        if self.decision_max_minutes < self.decision_min_minutes:
            raise ValueError('最大决策间隔不能小于最小间隔')
        for value in self.owner_ids + self.source_groups + ([self.bot_uin] if self.bot_uin else []):
            if not value.isdecimal() or int(value) <= 0:
                raise ValueError('QQ 号和群号必须为正整数文本')
        return self


class LifeConfig(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    life: Settings = Field(default_factory=Settings)

from typing import Annotated, List, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from ai.model_configuration import PROVIDER_URLS
from ai.model_pool import extra_body_for, requires_thinking


class ModelConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Literal["bailian", "volcengine", "openai"]
    model_names: List[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1)
    api_key: Optional[SecretStr] = None
    base_url: str = Field(default="", max_length=2048)
    enabled: bool = True
    thinking_enabled: Literal[False] = False
    # Compatible endpoints use different extensions. Keep this choice explicit.
    thinking_format: Literal["auto", "enable_thinking", "thinking", "reasoning_effort", "none"] = "auto"

    @field_validator("model_names")
    @classmethod
    def validate_name(cls, value):
        names = []
        seen = set()
        for name in value:
            name = name.strip()
            if not name or any(character.isspace() for character in name):
                raise ValueError("模型名不能为空或包含空白字符")
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names

    @model_validator(mode="after")
    def validate_off_parameters(self):
        for name in self.model_names:
            if not requires_thinking(name):
                extra_body_for(name, self.provider, thinking_format=self.thinking_format)
        return self

    @model_validator(mode="after")
    def validate_url(self):
        # Native modes always use their official endpoints. Existing workspace
        # endpoints can be retained on update by the API, or use compatible mode.
        if self.provider in PROVIDER_URLS:
            self.base_url = PROVIDER_URLS[self.provider]
            return self
        try:
            url = urlsplit(self.base_url)
            valid = (url.scheme in ("http", "https") and url.hostname and
                     not url.username and not url.password and not url.query and not url.fragment)
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Base URL 必须是有效的 HTTP(S) 地址，不能包含账号、查询参数或片段")
        self.base_url = self.base_url.rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            raise ValueError("Base URL 请填写 API 根地址（如 /v1），不要包含 /chat/completions")
        return self

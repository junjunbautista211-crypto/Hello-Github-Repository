from pydantic import BaseModel, Field

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)

class LoginIn(BaseModel):
    username: str
    password: str
    device_id: str = Field(min_length=8, max_length=160)

class ConversationIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    is_group: bool = True
    member_ids: list[int] = []

class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)

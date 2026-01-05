from pydantic import BaseModel, EmailStr
from typing import Optional

class SavedSearchCreate(BaseModel):
    user_email: EmailStr
    city: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None

class SavedSearchOut(SavedSearchCreate):
    id: int
    is_active: bool

    class Config:
        from_attributes = True
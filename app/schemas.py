# app/schemas.py
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any

# This defines the data coming IN from the frontend
class AlertRequest(BaseModel):
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    
    # We use a Dict for criteria so it's flexible
    # Example: {"min_price": 500000, "beds": 3, "zip": "98672"}
    criteria: Dict[str, Any] 

    class Config:
        from_attributes = True

# This defines data going OUT (if we return the saved search to the user)
class SavedSearchCreate(BaseModel):
    id: int
    is_active: bool
    criteria: Dict[str, Any]

    class Config:
        from_attributes = True
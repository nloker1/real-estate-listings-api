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

from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, date

class ListingImageBase(BaseModel):
    url: str
    order: int
    is_private: Optional[bool] = False

    class Config:
        from_attributes = True # updated for Pydantic v2 (use 'orm_mode = True' if on v1)

class ListingBase(BaseModel):
    mls_number: str
    price: Optional[int] = None
    status: Optional[str] = None
    detailed_status: Optional[str] = None
    
    # Address / Location
    address: Optional[str] = None
    city: Optional[str] = None
    zipcode: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    is_address_exposed: Optional[bool] = True
    
    # Basic Specs
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    lot_size_sqft: Optional[float] = None
    acreage: Optional[float] = None
    year_built: Optional[int] = None
    garage_spaces: Optional[float] = None # NEW

    # Descriptions
    property_type: Optional[str] = None
    property_sub_type: Optional[str] = None
    public_remarks: Optional[str] = None
    photo_url: Optional[str] = None
    
    # Agents
    listing_brokerage: Optional[str] = None
    list_agent_name: Optional[str] = None
    buyer_agent_name: Optional[str] = None # NEW
    
    # Financials / Legal (NEW)
    tax_annual_amount: Optional[int] = None
    association_fee: Optional[int] = None
    gross_income: Optional[int] = None
    
    # Details (NEW)
    cooling: Optional[str] = None
    heating: Optional[str] = None
    sewer: Optional[str] = None
    water_source: Optional[str] = None
    
    # Schools (NEW)
    elementary_school: Optional[str] = None
    middle_or_junior_school: Optional[str] = None
    high_school: Optional[str] = None
    
    # Timestamps
    days_on_market: Optional[int] = None
    last_updated: Optional[datetime] = None
    rmls_dom: Optional[int] = None
    rmls_cdom: Optional[int] = None

    images: List[ListingImageBase] = []

    class Config:
        from_attributes = True
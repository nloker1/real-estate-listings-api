from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Numeric, ForeignKey, JSON, Date, Text
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class Listing(Base):
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True, index=True)
    mls_number = Column(String, unique=True, index=True)

    # --- STATUS & VISIBILITY ---
    status = Column(String, index=True)  
    detailed_status = Column(String, nullable=True) 
    status_date = Column(DateTime, nullable=True)
    days_on_market = Column(Integer, nullable=True)
    is_published = Column(Boolean, default=True)
    
    # Sold Data (Optional for now since we are reverting filter)
    close_price = Column(Integer, nullable=True)
    close_date = Column(Date, nullable=True)

    # --- LOCATION ---
    address = Column(String)
    city = Column(String, index=True)
    zipcode = Column(String, index=True)
    lat = Column(Float)
    lon = Column(Float)
    is_address_exposed = Column(Boolean, default=True)
    
    # New Location Fields
    mls_area_major = Column(String, nullable=True)
    elementary_school = Column(String, nullable=True)
    middle_or_junior_school = Column(String, nullable=True) # New!
    high_school = Column(String, nullable=True)
    zoning = Column(String, nullable=True)

    # --- BASIC SPECS ---
    price = Column(Integer)
    beds = Column(Integer)
    baths = Column(Float)
    sqft = Column(Integer)
    lot_size_sqft = Column(Float)
    acreage = Column(Float)
    year_built = Column(Integer)
    garage_spaces = Column(Float, nullable=True) 

    # --- EXPANDED DETAILS ---
    property_type = Column(String)
    property_sub_type = Column(String)
    photo_url = Column(String)
    public_remarks = Column(Text)
    
    # Mechanicals
    cooling = Column(String, nullable=True)
    heating = Column(String, nullable=True)
    fuel_description = Column(String, nullable=True)
    roof = Column(String, nullable=True)
    sewer = Column(String, nullable=True)
    water_source = Column(String, nullable=True)
    utilities = Column(String, nullable=True)

    # --- AGENT DATA ---
    listing_brokerage = Column(String)
    list_agent_name = Column(String)
    buyer_agent_name = Column(String, nullable=True)
    attribution_contact = Column(String)

    # --- FINANCIALS ---
    tax_annual_amount = Column(Integer, nullable=True)
    tax_legal_description = Column(Text, nullable=True)
    association_fee = Column(Integer, nullable=True)
    association_yn = Column(Boolean, nullable=True)
    gross_income = Column(Integer, nullable=True)
    list_price_high = Column(Integer, nullable=True)
    list_price_low = Column(Integer, nullable=True)

    # --- SYSTEM ---
    is_new = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, nullable=True)

    images = relationship("ListingImage", back_populates="listing", cascade="all, delete-orphan")

class ListingImage(Base):
    __tablename__ = "listing_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    url = Column(String, nullable=False)
    order = Column(Integer, default=0)  # To keep the photos in the correct sequence
    is_private = Column(Boolean, nullable=True)

    # NEW: Back-reference to the main listing
    listing = relationship("Listing", back_populates="images")

class Lead(Base):
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True, nullable=True)
    email = Column(String, unique=True, index=True, nullable=True)
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship to searches
    searches = relationship("SavedSearch", back_populates="lead")

class SavedSearch(Base):
    __tablename__ = "saved_searches"
    
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"))
    
    # This stores the filters: {"min_price": 500000, "beds": 3, "zip": "98672"}
    criteria = Column(JSON, nullable=False)
    
    frequency = Column(String, default="instant") # "instant" or "daily"
    is_active = Column(Boolean, default=True)
    last_alert_sent = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    lead = relationship("Lead", back_populates="searches")

class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, index=True)
    search_id = Column(Integer, ForeignKey("saved_searches.id"))
    listing_id = Column(String, index=True) # The MLS Number
    user_email = Column(String, index=True)
    
    # Tracking Stats
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    opened_at = Column(DateTime(timezone=True), nullable=True)
    clicked_at = Column(DateTime(timezone=True), nullable=True)
    
    # Resend ID (Useful for debugging)
    message_id = Column(String, nullable=True)

    search = relationship("SavedSearch")



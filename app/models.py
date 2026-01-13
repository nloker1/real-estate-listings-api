from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class Listing(Base):
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mls_number = Column(String, unique=True, index=True)
    
    # Status Fields
    internal_status = Column(String, default='Active') # 'Active' or 'Inactive'
    mls_status = Column(String, nullable=True)     # 'Active', 'Pending', 'Closed'
    standard_status = Column(String, nullable=True)
    status_change_timestamp = Column(DateTime, nullable=True)
    
    photo_url = Column(String, nullable=True)
    address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    price = Column(Numeric, nullable=True)
    beds = Column(Integer, nullable=True)
    baths = Column(Float, nullable=True)
    sqft = Column(Integer, nullable=True)           # Maps to LivingArea
    year_built = Column(Integer, nullable=True)
    
    # Land Fields
    acreage = Column(Float, nullable=True)          # Maps to LotSizeAcres
    lot_size_sqft = Column(Float, nullable=True)    # Maps to LotSizeSquareFeet
    
    style = Column(String, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    is_new = Column(Boolean, default=True)
    is_address_exposed = Column(Boolean, default=False)
    listing_brokerage = Column(String, default='No Information Provided')
    list_agent_name = Column(String, nullable=True)
    property_type = Column(String, nullable=True)
    property_sub_type = Column(String, nullable=True)
    public_remarks = Column(String, nullable=True)
    attribution_contact = Column(String, nullable=True)
    
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
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

class SavedSearch(Base):
    __tablename__ = "saved_searches"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String, index=True, nullable=False)
    
    # Search Criteria
    city = Column(String, nullable=True)
    min_price = Column(Float, nullable=True)
    max_price = Column(Float, nullable=True)
    min_beds = Column(Integer, nullable=True)
    min_baths = Column(Float, nullable=True)
    
    # Notification Settings
    is_active = Column(Boolean, default=True)
    last_notified = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_modified = Column(DateTime(timezone=True), onupdate=func.now())

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



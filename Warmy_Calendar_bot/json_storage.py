"""
JSON-based local storage system for vehicle data.
Replaces Google Sheets API caching with persistent local storage.
"""

import json
import os
import datetime as dt
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from pathlib import Path

@dataclass
class VehicleEvent:
    event_type: str
    expires: Optional[str]  # ISO date string
    doc_links: List[str]
    last_updated: str  # ISO timestamp

@dataclass
class VehicleRecord:
    plate: str
    events: List[VehicleEvent]
    excluded: bool
    excluded_at: Optional[str]  # ISO timestamp
    excluded_by: Optional[str]
    last_seen: str  # ISO timestamp

class JSONStorage:
    def __init__(self, file_path: str = "/opt/render/project/src/vehicle_data.json"):
        self.file_path = file_path
        self.data = self._load_data()
    
    def _load_data(self) -> Dict[str, Any]:
        """Load data from JSON file, create empty structure if not exists"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    print(f"📋 Loaded vehicle data from {self.file_path}")
                    return data
        except Exception as e:
            print(f"⚠️ Error loading JSON data: {e}")
        
        # Return empty structure
        print(f"🆕 Creating new vehicle data structure")
        return {
            "last_updated": None,
            "vehicles": {}
        }
    
    def _save_data(self) -> bool:
        """Save data to JSON file"""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            print(f"💾 Saved vehicle data to {self.file_path}")
            return True
        except Exception as e:
            print(f"❌ Error saving JSON data: {e}")
            return False
    
    def update_vehicle_data(self, vehicles_data: List[tuple]) -> bool:
        """
        Update vehicle data from Google Sheets sync.
        vehicles_data: List of (plate, event_type, expiry_date, timestamp) tuples
        """
        # Convert to enhanced format for backward compatibility
        enhanced_data = []
        for plate, event_type, expiry_date, timestamp in vehicles_data:
            enhanced_data.append((plate, event_type, expiry_date, timestamp, []))
        return self.update_vehicle_data_enhanced(enhanced_data)
    
    def update_vehicle_data_enhanced(self, vehicles_data: List[tuple]) -> bool:
        """
        Update vehicle data from Google Sheets sync with document links.
        vehicles_data: List of (plate, event_type, expiry_date, timestamp, doc_links) tuples
        """
        from .data_model import latest_by_plate_event
        
        now = dt.datetime.now().isoformat()
        
        # First, collect all raw data
        all_raw_events = []
        for plate, event_type, expiry_date, timestamp, doc_links in vehicles_data:
            all_raw_events.append((plate, event_type, expiry_date, timestamp, doc_links))
        
        # Use latest_by_plate_event logic to get only the most recent entries
        # Convert to format expected by latest_by_plate_event
        tuples_for_latest = [(plate, event_type, expiry_date, timestamp) for plate, event_type, expiry_date, timestamp, doc_links in all_raw_events]
        latest_records = latest_by_plate_event(tuples_for_latest)
        
        # Create a lookup for document links
        doc_links_lookup = {}
        for plate, event_type, expiry_date, timestamp, doc_links in all_raw_events:
            key = (plate, event_type, expiry_date, timestamp)
            doc_links_lookup[key] = doc_links
        
        # Build new vehicles data using only latest records
        new_vehicles = {}
        for record in latest_records:
            plate = record.plate
            if plate not in new_vehicles:
                new_vehicles[plate] = {
                    "events": [],
                    "excluded": False,
                    "excluded_at": None,
                    "excluded_by": None,
                    "last_seen": now
                }
            
            # Find matching document links
            key = (record.plate, record.event_type, record.expiry_date, record.timestamp)
            doc_links = doc_links_lookup.get(key, [])
            
            # Add event
            event = {
                "event_type": record.event_type,
                "expires": record.expiry_date.isoformat() if record.expiry_date else None,
                "doc_links": doc_links,
                "last_updated": record.timestamp.isoformat() if record.timestamp else now
            }
            new_vehicles[plate]["events"].append(event)
        
        # Merge with existing data, preserving exclusions
        for plate, vehicle_data in new_vehicles.items():
            if plate in self.data["vehicles"]:
                # Preserve exclusion status
                existing = self.data["vehicles"][plate]
                vehicle_data["excluded"] = existing.get("excluded", False)
                vehicle_data["excluded_at"] = existing.get("excluded_at")
                vehicle_data["excluded_by"] = existing.get("excluded_by")
            
            self.data["vehicles"][plate] = vehicle_data
        
        # Remove vehicles not seen in latest sync (unless excluded)
        current_plates = set(new_vehicles.keys())
        to_remove = []
        for plate in self.data["vehicles"]:
            if plate not in current_plates and not self.data["vehicles"][plate].get("excluded", False):
                to_remove.append(plate)
        
        for plate in to_remove:
            del self.data["vehicles"][plate]
            print(f"🗑️ Removed outdated vehicle: {plate}")
        
        self.data["last_updated"] = now
        return self._save_data()
    
    def get_active_vehicles(self) -> Dict[str, Dict]:
        """Get all non-excluded vehicles"""
        active = {}
        for plate, vehicle in self.data["vehicles"].items():
            if not vehicle.get("excluded", False):
                active[plate] = vehicle
        return active
    
    def get_all_vehicles(self) -> Dict[str, Dict]:
        """Get all vehicles including excluded ones"""
        return self.data["vehicles"].copy()
    
    def exclude_vehicle(self, plate: str, excluded_by: str) -> bool:
        """Mark a vehicle as excluded"""
        if plate not in self.data["vehicles"]:
            return False
        
        self.data["vehicles"][plate]["excluded"] = True
        self.data["vehicles"][plate]["excluded_at"] = dt.datetime.now().isoformat()
        self.data["vehicles"][plate]["excluded_by"] = excluded_by
        
        return self._save_data()
    
    def restore_vehicle(self, plate: str) -> bool:
        """Restore an excluded vehicle"""
        if plate not in self.data["vehicles"]:
            return False
        
        self.data["vehicles"][plate]["excluded"] = False
        self.data["vehicles"][plate]["excluded_at"] = None
        self.data["vehicles"][plate]["excluded_by"] = None
        
        return self._save_data()
    
    def get_excluded_vehicles(self) -> Dict[str, Dict]:
        """Get all excluded vehicles"""
        excluded = {}
        for plate, vehicle in self.data["vehicles"].items():
            if vehicle.get("excluded", False):
                excluded[plate] = vehicle
        return excluded
    
    def get_last_updated(self) -> Optional[str]:
        """Get last update timestamp"""
        return self.data.get("last_updated")
    
    def is_data_fresh(self, max_age_hours: int = 25) -> bool:
        """Check if data is fresh enough"""
        last_updated = self.get_last_updated()
        if not last_updated:
            return False
        
        try:
            last_update_time = dt.datetime.fromisoformat(last_updated)
            age = dt.datetime.now() - last_update_time
            return age.total_seconds() < (max_age_hours * 3600)
        except Exception:
            return False
    
    def get_stats(self) -> Dict[str, int]:
        """Get storage statistics"""
        total = len(self.data["vehicles"])
        excluded = len(self.get_excluded_vehicles())
        active = total - excluded
        
        return {
            "total_vehicles": total,
            "active_vehicles": active,
            "excluded_vehicles": excluded
        }

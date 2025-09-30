"""
Data synchronization between Google Sheets and local JSON storage.
Handles daily updates and manual refresh commands.
"""

import asyncio
import subprocess
import datetime as dt
from typing import List, Tuple, Optional
from .config import load_config
from .sheets_client import SheetsClient
from .data_model import normalize_event
from .json_storage import JSONStorage
from .users_repo import UsersRepo

cfg = load_config()

class DataSync:
    def __init__(self):
        self.storage = JSONStorage()
    
    async def sync_from_google_sheets(self, force: bool = False) -> Tuple[bool, str]:
        """
        Sync data from Google Sheets to local JSON storage.
        Returns (success, message)
        """
        try:
            if not (cfg.spreadsheet_id and cfg.google_credentials_path):
                return False, "⚠️ Google Sheets configuration missing"
            
            print("🔄 Starting Google Sheets sync...")
            
            # Read data from Google Sheets
            client = SheetsClient(cfg.spreadsheet_id, cfg.google_credentials_path)
            raw = client.read_data_rows(cfg.data_tab_name)
            
            # Process and normalize data with document links
            raw_tuples = []
            for r in raw:
                ev = normalize_event(r.event_raw)
                if not ev:
                    continue
                exp = SheetsClient.parse_mmddyyyy(r.expiry_raw)
                ts = None
                if r.timestamp:
                    try:
                        ts = dt.datetime.strptime(r.timestamp, "%m/%d/%Y %H:%M:%S")
                    except Exception:
                        ts = None
                
                # Collect document links
                doc_links = []
                if r.doc1:
                    doc_links.append(r.doc1)
                if r.doc2:
                    doc_links.append(r.doc2)
                
                raw_tuples.append((r.plate, ev, exp, ts, doc_links))
            
            # Update JSON storage with enhanced data
            success = self.storage.update_vehicle_data_enhanced(raw_tuples)
            
            if success:
                stats = self.storage.get_stats()
                message = f"✅ Sync completed: {stats['active_vehicles']} active vehicles, {stats['excluded_vehicles']} excluded"
                print(message)
                
                # Auto-backup to GitHub
                await self._backup_to_github()
                
                return True, message
            else:
                return False, "❌ Failed to save data to JSON storage"
                
        except Exception as e:
            error_msg = f"❌ Sync failed: {str(e)}"
            print(error_msg)
            return False, error_msg
    
    async def _backup_to_github(self) -> bool:
        """Backup JSON data to GitHub repository"""
        try:
            print("📤 Backing up data to GitHub...")
            
            # Git commands to commit and push the JSON file
            commands = [
                ["git", "add", "vehicle_data.json"],
                ["git", "commit", "-m", f"Auto-backup vehicle data - {dt.datetime.now().isoformat()}"],
                ["git", "push", "origin", "main"]
            ]
            
            for cmd in commands:
                result = subprocess.run(cmd, capture_output=True, text=True, cwd="/opt/render/project/src")
                if result.returncode != 0 and "nothing to commit" not in result.stdout:
                    print(f"⚠️ Git command failed: {' '.join(cmd)} - {result.stderr}")
                    return False
            
            print("✅ Data backed up to GitHub")
            return True
            
        except Exception as e:
            print(f"❌ GitHub backup failed: {e}")
            return False
    
    def get_processed_data_for_reminders(self) -> List[Tuple]:
        """Get processed vehicle data for daily reminders"""
        active_vehicles = self.storage.get_active_vehicles()
        tuples = []
        
        for plate, vehicle_data in active_vehicles.items():
            for event in vehicle_data["events"]:
                exp_date = None
                if event["expires"]:
                    try:
                        exp_date = dt.datetime.fromisoformat(event["expires"]).date()
                    except Exception:
                        continue
                
                ts = None
                if event["last_updated"]:
                    try:
                        ts = dt.datetime.fromisoformat(event["last_updated"])
                    except Exception:
                        pass
                
                tuples.append((plate, event["event_type"], exp_date, ts))
        
        return tuples
    
    def get_vehicle_details(self, plate: str) -> Optional[dict]:
        """Get detailed information for a specific vehicle"""
        vehicles = self.storage.get_all_vehicles()
        return vehicles.get(plate.upper())
    
    def get_all_active_plates(self) -> List[str]:
        """Get list of all active (non-excluded) plate numbers"""
        active_vehicles = self.storage.get_active_vehicles()
        return sorted(active_vehicles.keys())
    
    def exclude_vehicle(self, plate: str, excluded_by: str) -> Tuple[bool, str]:
        """Exclude a vehicle from future reports"""
        plate = plate.upper()
        vehicles = self.storage.get_all_vehicles()
        
        if plate not in vehicles:
            return False, f"❌ Vehicle {plate} not found in data"
        
        if vehicles[plate].get("excluded", False):
            return False, f"⚠️ Vehicle {plate} is already excluded"
        
        success = self.storage.exclude_vehicle(plate, excluded_by)
        if success:
            return True, f"✅ Vehicle {plate} excluded from future reports"
        else:
            return False, f"❌ Failed to exclude vehicle {plate}"
    
    def get_excluded_vehicles_list(self) -> str:
        """Get formatted list of excluded vehicles"""
        excluded = self.storage.get_excluded_vehicles()
        
        if not excluded:
            return "📋 No vehicles are currently excluded"
        
        lines = ["📋 Excluded vehicles:"]
        for plate, vehicle_data in excluded.items():
            excluded_at = vehicle_data.get("excluded_at", "")
            excluded_by = vehicle_data.get("excluded_by", "unknown")
            
            if excluded_at:
                try:
                    date_str = dt.datetime.fromisoformat(excluded_at).strftime("%Y-%m-%d")
                    lines.append(f"• {plate} (excluded {date_str} by {excluded_by})")
                except Exception:
                    lines.append(f"• {plate} (excluded by {excluded_by})")
            else:
                lines.append(f"• {plate} (excluded by {excluded_by})")
        
        return "\n".join(lines)
    
    def is_data_available(self) -> bool:
        """Check if we have usable data"""
        return len(self.storage.get_all_vehicles()) > 0
    
    def get_data_status(self) -> str:
        """Get human-readable data status"""
        last_updated = self.storage.get_last_updated()
        stats = self.storage.get_stats()
        
        if not last_updated:
            return "❌ No data available - run /update to sync from Google Sheets"
        
        try:
            update_time = dt.datetime.fromisoformat(last_updated)
            age = dt.datetime.now() - update_time
            age_str = f"{int(age.total_seconds() / 3600)}h {int((age.total_seconds() % 3600) / 60)}m ago"
            
            return f"📊 Data: {stats['active_vehicles']} active, {stats['excluded_vehicles']} excluded (updated {age_str})"
        except Exception:
            return f"📊 Data: {stats['active_vehicles']} active, {stats['excluded_vehicles']} excluded"

# Global instance
data_sync = DataSync()

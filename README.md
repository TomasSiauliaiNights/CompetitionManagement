# Robotics Tournament Manager v4

Tournament management for **Line Following**, **Folkrace**, and **Fire Sister** with PostgreSQL backend, ESP32 hardware timer, OBS overlay, and optional Google Sheets backup sync.

## Architecture

```
Excel (.xlsx)  ──import──►  PostgreSQL  ◄──live read/write──►  Python GUI
                               │                                   │
                               │ background sync (30s)             ├──► OBS Overlay (HTML)
                               ▼                                   ├──► Second Screen
                         Google Sheets                             └──► ESP32 (Serial)
                         (backup/view)
```

**PostgreSQL** is the primary data store. All reads and writes happen against the database in real-time. Google Sheets acts as an optional background backup that syncs every 30 seconds.

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set up PostgreSQL
```bash
# Create the database
createdb tournament

# OR with the schema file (optional — the app auto-creates tables)
psql -U postgres -d tournament -f schema.sql
```

### 3. Run
```bash
python tournament_manager.py
```

### 4. First-time setup
1. Click **⚙** to open Settings → enter your PostgreSQL connection details
2. Click **🗄 Connect DB**
3. Click **📥 Import Excel** → select your tournament `.xlsx` file to load robots and existing scores
4. (Optional) Click **☁ Start GSheets Sync** to enable background backup

## Workflow

### Line Following
1. Connect DB → lookup robot by number
2. Press READY → sensor triggers timer (or Spacebar for manual)
3. Press STOP → CONFIRM to save time to DB
4. Best time computed automatically from all trials

### Folkrace
1. Select group from dropdown → robot entries load from DB
2. Press START for 3-2-1 countdown
3. Enter R1/R2/R3 scores → SAVE SCORES writes to DB

### Fire Sister
1. Lookup robot → START for 3-minute countdown
2. Enter points → CONFIRM POINTS saves to DB
3. Best = highest points across all trials

## Google Sheets Backup

The Google Sheets sync is **one-way push** from DB to Sheets. It runs in a background thread every 30 seconds and overwrites 3 sheets: "Line Following", "Fire Sister", "Folkrace".

Requirements:
- Must be a **native Google Sheet** (not an uploaded .xlsx)
- Service account JSON credentials
- Spreadsheet shared with the service account email as Editor

## Files

```
tournament_manager.py    # Main GUI
db.py                    # PostgreSQL + Excel import + GSheets sync
schema.sql               # DB schema (auto-created by app)
overlay/
  overlay.html           # OBS browser source
  overlay_data.js        # Written by GUI, polled by overlay
esp32/
  timer_controller.ino   # ESP32 firmware
```

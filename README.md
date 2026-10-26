# Pakistan-National-Grid-Mapping

Pakistan Custom Spatial Grid Library: Architectural Specification


1. Executive Summary & Core Objective

The purpose of this library is to replace traditional, unstructured street text addresses with a hyper-efficient, highly deterministic Discrete Global Grid System (DGGS) tailored specifically for Pakistan's borders.
By mapping raw GPS coordinates into fixed-size grid matrices and bit-packing the indices, the library eliminates address parsing ambiguity, clusters shipping routes automatically, and links a user's geographical location directly to their CNIC (Identity Card) number inside your database.

2. Global Bounding Box & Shard Partitioning

To eliminate processing overhead and reduce database index sizes, the master bounding box of Pakistan is split into 4 horizontal quadrants (shards) based on national geographic midlines. Each quadrant functions as an independent grid coordinate sheet.

📐 Global Matrix Boundaries

• South-West Anchor (Tropic of Cancer): Latitude 23.436° N, Longitude 60.800° E
• North-East Anchor (China Border): Latitude 37.080° N, Longitude 77.000° E
• Global Midlines (The Splits): Latitude 30.258° N | Longitude 68.900° E

🗂️ Quadrant Table Mapping


Quadrant Shard Code	Regional Alignment	Database Shard Table Target
00
	South-West (Gwadar / Coast)	master_cells_q00
01
	North-West (Quetta / Afghan Border)	master_cells_q01
10
	South-East (Karachi / Sindh)	master_cells_q10
11
	North-East (Lahore / Islamabad / GB)	master_cells_q11


3. Two-Layer Grid Hierarchy

To handle both macro-logistics routing (van dispatching) and micro-logistics routing (final package drop-off), the library defines two rigid geometric layers. Earth curvature errors are corrected programmatically at the library level using a sinusoidal metric projection to ensure cells touch perfectly edge-to-edge with no dead space.
+───────────────────────────────────────────────────────────+
│ MACRO CELL BLOCK [1km × 1km]                              │
│                                                           │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │
│ │ Sub-Cell│ │         │ │         │ │         │ │         │ │
│ │ (0,4)   │ │         │ │         │ │         │ │         │ │
│ ├─────────┤ └─────────┘ └─────────┘ └─────────┘ └─────────┘ │
│ │   ...   │                                               │ │
│ ├─────────┤                                               │ │
│ │ Sub-Cell│                                               │ │
│ │ (0,0)   │ ◄── [200m × 200m Block Center]                │ │
│ └─────────┴───────────────────────────────────────────────┘ │
│ (Local X=0, Y=0)                                          │
+───────────────────────────────────────────────────────────+

🟩 Macro Cells (1km × 1km)

• Purpose: Fleet dispatching, sorting delivery bags, and inventory fulfillment planning.
• Layout: Sliced into continuous 1,000-meter increments tracking horizontally (Local X) and vertically (Local Y) starting from (0,0) at each quadrant's individual bottom-left origin.
• Scale: Max span inside a single quadrant is under 1,024 km, keeping the grid bounds compact.

🟦 Sub-Cells (200m × 200m)

• Purpose: Precise final drop-off location clustering (City Block / Sector Sub-Block level).
• Layout: Every Macro Cell is divided into a rigid 5 × 5 matrix along each axis, producing exactly 25 sub-cells per kilometer block.
• Scale: Sub-cell indices (Sub_X, Sub_Y) range purely from 0 to 4.

4. Digital Token & Bit-Packing Architecture

Rather than storing coordinates as heavy, performance-sapping text strings (e.g., "004512"), identifiers are compressed into memory-optimized integers.

🔑 Cell ID (16-bit Local Integer)

Unique only within its respective quadrant table. It packs the local kilometer positions into a 2-byte storage unit using a 10-bit offset bit-shift.
• Mathematical Formula:
\(\text{cell\_id}=(\text{Local\ X}\ll 10)\mid \text{Local\ Y}\)
• Database Data Type: SMALLINT (2 Bytes).

🔑 Sub-Cell ID (8-bit Tiny Integer)

Unique only within its parent macro cell. It maps the 5 × 5 grid sequence into a 1-byte relative position.
• Mathematical Formula:
\(\text{sub\_cell\_id}=(\text{Sub\_X}\times 5)+\text{Sub\_Y}\)
• Database Data Type: TINYINT (1 Byte, range 0–24).

🌍 The Global Complete Address Token Tuple

Across the entire application framework, any specific 200m city block in Pakistan is completely defined by a 3-part spatial composite key:
\(\big[\,\text{Quadrant\ Shard}\;\mathbf{,}\;\text{cell\_id}\;\mathbf{,}\;\text{sub\_cell\_id}\,\big]\)

5. Storage Sizing & Infrastructure Footprint

Because the engine uses an optimized relational schema layout and basic integer keys, database size remains microscopic even as your operations scale to millions of users.
• Server Core RAM Footprint: < 50 Megabytes. Lookups do not require heavy, active spatial processing elements (like PostGIS). The entire system relies on blazing-fast CPU arithmetic registers.
• Data Record Size Per User Address: ~100 Bytes (including UUIDs, CNIC strings, and raw coordinate backups).
• Storage Metrics Scale:
	• 1,000,000 Registered Profiles: ~100 Megabytes of drive space.
	• 10,000,000 Registered Profiles: ~1 Gigabyte of drive space.

6. App Interaction Lifecycle (The Logistics Flow)

[ User App Profile Screen ]
  ├── 1. User inputs: CNIC, Text Address Notes
  └── 2. User sets manual Map Pin (Lat/Lon Map Drop)
            │
            ▼
[ Mobile App ──► Sends Payload to API Backend ]
            │
            ▼
[ Core Grid Mapping Library Processing ]
  ├── 3. Compares Lat/Lon against midlines to find Shard ('00', '01', '10', '11')
  ├── 4. Translates GPS degree vectors into metric distance from Shard Origin
  ├── 5. Computes Local X/Y to pack local 16-bit cell_id (SMALLINT)
  ├── 6. Computes Sub X/Y to generate local 8-bit sub_cell_id (TINYINT)
            │
            ▼
[ Database Storage Committal ]
  └── 7. Saves row containing [User_ID, Shard, Cell_ID, Sub_Cell_ID, CNIC, Notes]
            │
            ▼
[ Courier App Routing Lookup ]
  └── 8. Driver queries database. System returns immutable, pre-calculated 
         fixed center GPS coordinates of that block for absolute route stability.
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
import traceback
import zipfile
from datetime import datetime, timedelta
from typing import Optional

import mysql.connector
import pandas as pd
import pyodbc
from flask import Blueprint, make_response, render_template, request, jsonify, Response, send_file, session
from PIL import Image
from werkzeug.utils import secure_filename

# Import ATC script
try:
    from .transactions_atc import process_atcrep_template
except ImportError:
    from transactions_atc import process_atcrep_template

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import SQLconnect from portal package
try:
    from portal.SQLconnection import SQLconnect
except ImportError:
    SQLconnect = None

transactions_bp = Blueprint('transactions', __name__)

def _is_blank(val: str) -> bool:
    """Return True if a string value represents an empty/missing cell."""
    return val.lower() in ('', 'nan', 'none')




NETWORK_IMAGE_PATH = r'\\mgsvr03\catalog'


def _best_odbc_driver() -> Optional[str]:
    """Return the highest-priority installed SQL Server ODBC driver, or None."""
    drivers = [d for d in pyodbc.drivers() if 'ODBC Driver' in d and 'SQL Server' in d]
    for version in ('18', '17', '13'):
        for d in drivers:
            if version in d:
                return d
    return drivers[0] if drivers else None


# ==============================================================
# SHARED: get a direct Windows-Auth connection to Barcodes DB
# ==============================================================
def _get_barcodes_conn():
    # Primary: direct Windows Auth to MGSVR14 (bypasses SQLconnect hang)
    driver = _best_odbc_driver()
    if driver:
        try:
            conn_str = f"DRIVER={{{driver}}};SERVER=MGSVR14;DATABASE=Barcodes;Trusted_Connection=yes;"
            if '18' in driver:
                conn_str += 'TrustServerCertificate=yes;'
            conn = pyodbc.connect(conn_str, timeout=5)
            logger.info('Barcodes DB: connected via direct pyodbc')
            return conn, conn.cursor()
        except Exception as e:
            logger.warning(f'Barcodes DB direct connection failed: {e}')

    # Fallback: via SQLconnect registry
    if SQLconnect:
        try:
            c, cur, _ = SQLconnect('Barcodes', 'DSRT')
            if c is not None:
                return c, cur
        except Exception as e:
            logger.error(f'SQLconnect fallback failed: {e}')

    return None, None


# ==============================================================
# MASTER CHAIN MAPPINGS
# Add new stores here. Format: 'EXCEL COLUMN HEADER': 'DB_TARGET_KEY'
# ==============================================================
CHAIN_MAPPINGS = {
    'ALTURAS': {
        'SKU/ITEM CODE': 'ITEM_CODE',
        'BARCODE':       'BARCODE',
        'DESCRIPTION':   'DESC',
        'VENDOR NO.':    'VENDOR'
    },
    'KCC': {
        'BARCODE':       'BARCODE',
        'DESCRIPTION':   'DESC',
        'SKU':           'SKU',
        'ITEM CODE':     'ITEM_CODE',
        'BRAND':         'BRAND_CODE'
    },
    'RDS': {
        'SKU NO.':          'BARCODE',
        'VENDOR PART #':    'ITEM_CODE',
        'ITEM DESCRIPTION': 'DESC',
        'BRAND':            'BRAND_CODE'
    },
    'RUSTANS': {
        'RCC SKU':          'BARCODE',
        'VENDOR ITEM CODE': 'ITEM_CODE'
    },
    'GGRAND': {
        'BRAND':       'BRAND_CODE',
        'DESCRIPTION': 'DESC',
        'SKU':         'ITEM_CODE', # Mapped to ITEM_CODE for database validation
        'BARCODE':     'BARCODE'
    },
    'GCAP': {
        'BRAND':        'BRAND_CODE',
        'ITEM CODE':    'ITEM_CODE',
        'DESCRIPTION':  'DESC',
        'GCAP BARCODE': 'BARCODE'
    },
    'SM': {
        'ITEM':   'ITEM_CODE',
        'SM UPC': 'BARCODE'
    }
}


# ==============================================================
# STAGE 1 — TEMPLATE DETECTION (THE SNIFFER)
# FIXED: Exact header matching + Explicitly reading Sheet 0
# ==============================================================
def detect_template_type(file_path: str) -> str:
    try:
        is_csv = file_path.lower().endswith('.csv')
        
        # FIX: Explicitly added sheet_name=0 to force it to only read the first sheet
        df_head = pd.read_csv(file_path, nrows=30, header=None, encoding='utf-8', encoding_errors='ignore') if is_csv else pd.read_excel(file_path, sheet_name=0, nrows=30, header=None)
        
        best_match = 'UNKNOWN'
        max_matches = 0
        
        for _, row in df_head.iterrows():
            # Clean the row values for exact matching
            row_vals = [str(v).strip().upper() for v in row.values if pd.notna(v)]
            row_vals_no_spaces = [v.replace(" ", "") for v in row_vals]
            
            for chain, mapping in CHAIN_MAPPINGS.items():
                matches = 0
                for expected in mapping.keys():
                    exp_upper = str(expected).upper()
                    exp_no_spaces = exp_upper.replace(" ", "")
                    
                    # FIX: Exact match instead of partial match prevents KCC from stealing other templates
                    if exp_upper in row_vals or exp_no_spaces in row_vals_no_spaces:
                        matches += 1
                
                # Tie-breaker logic: Pick the template with the highest match percentage
                match_percentage = matches / len(mapping) if len(mapping) > 0 else 0
                
                if matches >= 2 and matches > max_matches:
                    max_matches = matches
                    best_match = chain
                elif matches >= 2 and matches == max_matches:
                    current_best_mapping = CHAIN_MAPPINGS[best_match]
                    current_best_pct = max_matches / len(current_best_mapping)
                    if match_percentage > current_best_pct:
                        best_match = chain
                    
        # Fallback for old SM template (82 columns, no headers)
        if best_match == 'UNKNOWN':
            df_full = pd.read_csv(file_path, header=None, nrows=1, encoding='utf-8', encoding_errors='ignore') if is_csv else pd.read_excel(file_path, sheet_name=0, header=None, nrows=1)
            if len(df_full.columns) >= 82:
                return 'SM'
                
        return best_match

    except Exception as e:
        logger.error(f"Template detection failed: {e}")
        return 'UNKNOWN'


# ==============================================================
# STAGE 2 — PARSE ROWS (THE MAPPER)
# FIXED: Added dtype=str to prevent dropping leading zeros
# ==============================================================
def parse_sku_template(file_path: str, template_type: str) -> list:
    extracted_data = []
    is_csv = file_path.lower().endswith('.csv')
    
    try:
        if template_type not in CHAIN_MAPPINGS:
            if template_type == 'SM':
                # Force dtype=str to read as text
                df = pd.read_csv(file_path, header=None, dtype=str, encoding='utf-8', encoding_errors='ignore') if is_csv else pd.read_excel(file_path, sheet_name=0, header=None, dtype=str)
                for _, row in df.iterrows():
                    extracted_data.append({
                        'ITEM_CODE': str(row.iloc[0]).strip() if len(row) > 0 else '',
                        'BARCODE': str(row.iloc[81]).strip() if len(row) > 81 else '',
                    })
            return extracted_data

        mapping = CHAIN_MAPPINGS[template_type]
        
        # Force dtype=str to read as text and preserve exact barcode formatting
        df_full = pd.read_csv(file_path, header=None, dtype=str, encoding='utf-8', encoding_errors='ignore') if is_csv else pd.read_excel(file_path, sheet_name=0, header=None, dtype=str)
        
        header_idx = -1
        max_matches = 0
        
        for idx in range(min(50, len(df_full))):
            row_vals = [str(v).strip().upper() for v in df_full.iloc[idx].values if pd.notna(v)]
            row_vals_no_spaces = [v.replace(" ", "") for v in row_vals]
            
            matches = 0
            for expected in mapping.keys():
                exp_upper = str(expected).upper()
                exp_no_spaces = exp_upper.replace(" ", "")
                if exp_upper in row_vals or exp_no_spaces in row_vals_no_spaces:
                    matches += 1
            
            if matches > max_matches:
                max_matches = matches
                header_idx = idx
        
        if header_idx == -1 or max_matches == 0:
            return extracted_data

        raw_columns = [str(c).strip().upper() if pd.notna(c) else f"UNNAMED_{i}" for i, c in enumerate(df_full.iloc[header_idx].values)]
        df_full.columns = raw_columns
        df_data = df_full.iloc[header_idx + 1:].copy()
        
        col_map = {}
        for source_key, target_key in mapping.items():
            expected = str(source_key).upper()
            for col in df_data.columns:
                if expected == col or expected.replace(" ", "") == col.replace(" ", ""):
                    col_map[col] = target_key
                    break

        for _, row in df_data.iterrows():
            row_dict = {'ITEM_CODE': '', 'BARCODE': ''}
            has_data = False
            
            for file_col, target_key in col_map.items():
                val = str(row[file_col]).strip()
                if val.lower() not in ('nan', 'none', ''):
                    row_dict[target_key] = val
                    has_data = True
            
            if not row_dict.get('ITEM_CODE') and row_dict.get('SKU'):
                 row_dict['ITEM_CODE'] = row_dict['SKU']
                 
            if has_data:
                extracted_data.append(row_dict)

    except Exception as e:
        logger.error(f"parse_sku_template error [{template_type}]: {e}")

    return extracted_data


# ==============================================================
# STAGE 3 — NORMALIZE
# FIXED: Eradicates the ".0" decimal artifact from Excel floats
# ==============================================================
def normalize_rows(parsed_data: list) -> list:
    normalized = []
    for row in parsed_data:
        item_raw = str(row.get('ITEM_CODE', '')).strip()
        bar_raw  = str(row.get('BARCODE', '')).strip()

        # Strip trailing ".0" float artifact (e.g. Excel reads "007890" as 7890.0)
        bar_raw  = re.sub(r'\.0+$', '', bar_raw)
        item_raw = re.sub(r'\.0+$', '', item_raw)

        item_chk = item_raw.lower()
        bar_chk  = bar_raw.lower()
        if _is_blank(item_chk) and _is_blank(bar_chk):
            continue

        item_clean = item_raw.upper()
        # Leading zeros are preserved — do NOT convert to int()
        # NICREP item codes like '007890' must stay as '007890'

        # Only keep digits, but now leading zeros will survive!
        bar_clean = ''.join(c for c in bar_raw if c.isdigit())

        normalized_row = dict(row)
        normalized_row['ITEM_CODE']     = item_clean
        normalized_row['BARCODE']       = bar_clean
        normalized_row['ITEM_CODE_RAW'] = item_raw
        normalized_row['BARCODE_RAW']   = bar_raw

        normalized.append(normalized_row)

    return normalized


# ==============================================================
# STAGE 4 — VALIDATE ROWS (LOCAL DB AUDIT)
# BASED EXCLUSIVELY ON dbo.barcodes (NO NICREP)
# ==============================================================
def validate_rows(parsed_data: list, template_type: str) -> tuple:
    results       = []
    db_error      = None

    # Normalize first
    parsed_data = normalize_rows(parsed_data)

    # CONNECT TO LOCAL BARCODES DB ONLY
    conn, cursor = _get_barcodes_conn()
    if conn is None:
        db_error = "Could not connect to Barcodes Database"
        logger.error(db_error)

    seen_barcodes = {}
    for row in parsed_data:
        item_code = row.get('ITEM_CODE', '').strip()
        barcode   = row.get('BARCODE', '').strip()
        result    = dict(row)

        # VR-002: Empty fields
        if not barcode or _is_blank(barcode.lower()):
            result.update({'status': 'rejected', 'reason': 'VR-002: Barcode is empty'}); results.append(result); continue

        if not item_code or _is_blank(item_code.lower()):
            result.update({'status': 'rejected', 'reason': 'VR-002: Item Code is empty'}); results.append(result); continue

        # VR-004: Duplicate barcode within this specific upload file
        if barcode in seen_barcodes:
            result.update({'status': 'duplicate', 'reason': 'VR-004: Duplicate barcode in this file'}); results.append(result); continue
        seen_barcodes[barcode] = True

        # LOCAL DB Master checks
        if conn and cursor:
            try:
                # Look for the Barcode in the local dbo.barcodes table
                cursor.execute(
                    'SELECT ITEM_CODE FROM dbo.barcodes WHERE BARCODE = ?',
                    (barcode,)
                )
                db_row = cursor.fetchone()

                if not db_row:
                    # Barcode does not exist yet -> Mark as NEW (Ready for manual commit)
                    result.update({
                        'status': 'update',  # We keep the 'update' key so the frontend commit button picks it up
                        'reason': 'New Barcode - Ready to Add to Database'
                    })
                else:
                    db_item_code = str(db_row[0]).strip()
                    db_normalized = db_item_code.upper()
                    csv_normalized = item_code.upper()

                    if db_normalized == csv_normalized:
                        # Barcode already exists and matches the Item Code
                        result.update({
                            'status': 'ok',
                            'reason': 'Exists in Database - Already Validated'
                        })
                    else:
                        # Barcode exists but is assigned to a different Item Code!
                        result.update({
                            'status': 'conflict',
                            'reason': f'Mismatch: Database says this Barcode belongs to Item [{db_item_code}]'
                        })

            except Exception as e:
                result.update({'status': 'rejected', 'reason': f'DB Error: {str(e)}'})
        else:
            result.update({
                'status': 'db_offline',
                'reason': 'Validation Offline — Cannot reach Database'
            })

        results.append(result)

    if conn:
        conn.close()

    db_online = (conn is not None and db_error is None)
    return results, db_online, db_error


# ==============================================================
# STAGE 5 — COMMIT (MANUAL SAVE NEW BARCODES)
# PREVENTS DUPLICATES AND KEEPS EXISTING RECORDS
# ==============================================================
def commit_rows_to_db(rows: list, committed_by: str) -> dict:
    conn, cursor = _get_barcodes_conn()

    if conn is None:
        return {
            'success': False, 'committed': 0, 'skipped': 0, 'errors': [],
            'message': 'Cannot commit — Barcodes DB is unreachable'
        }

    committed = 0; skipped = 0; errors = []

    try:
        for row in rows:
            if row.get('status') != 'update':
                skipped += 1
                continue
            
            item_code = row.get('ITEM_CODE', '').strip()
            barcode   = row.get('BARCODE', '').strip()
            desc      = row.get('DESC', '').strip()
            vendor    = row.get('VENDOR', '').strip()
            brand     = row.get('BRAND_CODE', '').strip()
            sku       = row.get('SKU', '').strip()

            try:
                # INSERT ONLY IF NOT EXISTS (Prevents duplicates completely)
                cursor.execute("""
                    IF NOT EXISTS (SELECT 1 FROM dbo.barcodes WHERE BARCODE = ?)
                    BEGIN
                        INSERT INTO dbo.barcodes (ITEM_CODE, BARCODE, [DESC], VENDOR, BRAND_CODE, SKU, DATEADDED)
                        VALUES (?, ?, ?, ?, ?, ?, GETDATE())
                    END
                """, (
                    barcode, # 1. For EXISTS check
                    item_code, barcode, desc, vendor, brand, sku # 2. For INSERT
                ))
                
                committed += 1
            except Exception as e:
                errors.append({'BARCODE': barcode, 'error': str(e)})

        conn.commit()

    except Exception as e:
        errors.append({'BARCODE': 'BATCH', 'error': str(e)})
        try: conn.rollback()
        except Exception: pass
    finally:
        conn.close()

    _write_audit_log(rows, committed_by, committed, errors)

    return {
        'success': len(errors) == 0,
        'committed': committed,
        'skipped': skipped,
        'errors': errors,
        'message': f'{committed} new barcode(s) saved, {skipped} skipped, {len(errors)} error(s)'
    }


# ==============================================================
# STAGE 6 — AUDIT LOG
# Auto-creates dbo.barcode_audit_log and writes every commit.
# ==============================================================
def _write_audit_log(rows: list, committed_by: str, committed_count: int, errors: list):
    conn, cursor = _get_barcodes_conn()
    if conn is None:
        logger.error("Audit log skipped — cannot connect to Barcodes DB")
        return

    try:
        cursor.execute("""
            IF NOT EXISTS (
                SELECT 1 FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'barcode_audit_log'
            )
            CREATE TABLE dbo.barcode_audit_log (
                id            INT IDENTITY(1,1) PRIMARY KEY,
                committed_at  DATETIME         DEFAULT GETDATE(),
                committed_by  NVARCHAR(100),
                item_code     NVARCHAR(100),
                barcode_new   NVARCHAR(100),
                action        NVARCHAR(50),
                notes         NVARCHAR(500)
            )
        """)

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for row in rows:
            if row.get('status') != 'update':
                continue

            had_error = any(e.get('ITEM_CODE') == row.get('ITEM_CODE') for e in errors)
            action    = 'ERROR' if had_error else 'COMMITTED'

            cursor.execute("""
                INSERT INTO dbo.barcode_audit_log
                        (committed_at, committed_by, item_code, barcode_new, action, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    now,
                    committed_by,
                    row.get('ITEM_CODE', ''),
                    row.get('BARCODE', ''),
                    action,
                    row.get('reason', '')
                ))

        conn.commit()
        logger.info(f"Audit log: {committed_count} entries written by {committed_by}")

    except Exception as e:
        logger.error(f"Audit log write failed: {e}")
    finally:
        conn.close()


# ==============================================================
# ROUTES — BARCODE VALIDATION
# ==============================================================

@transactions_bp.route('/validate_barcode', methods=['GET'])
def validate_barcode_page():
    """Render the barcode validation UI."""
    if not session.get('sdr_loggedin'):
        return render_template('home.html')
    return render_template('validate_barcode.html')


@transactions_bp.route('/api/detect_template', methods=['POST'])
def detect_template():
    """Stage 1: Accept a file and return the auto-detected template type."""
    if not session.get('sdr_loggedin'):
        return jsonify({'error': 'Not authenticated'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    temp_filepath = None
    try:
        # FIXED: mkstemp prevents the Windows file lock crash
        filename = secure_filename(file.filename)
        suffix = os.path.splitext(filename)[1]
        fd, temp_filepath = tempfile.mkstemp(suffix=suffix)
        os.close(fd) 
        
        file.save(temp_filepath)

        detected = detect_template_type(temp_filepath)
        return jsonify({'success': True, 'template_type': detected, 'filename': filename}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)


@transactions_bp.route('/api/parse_sku_file', methods=['POST'])
def parse_sku_file():
    """Stages 2–4: Upload → parse → normalize → validate → return preview."""
    if not session.get('sdr_loggedin'):
        return jsonify({'error': 'Not authenticated'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file          = request.files['file']
    template_type = request.form.get('template_type', '').upper().strip()

    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
    if not template_type:
        return jsonify({'error': 'Missing template_type'}), 400

    temp_filepath = None
    try:
        # FIXED: mkstemp prevents the Windows file lock crash
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({'error': 'Invalid filename'}), 400

        suffix = os.path.splitext(filename)[1]
        fd, temp_filepath = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        
        file.save(temp_filepath)

        parsed_data = parse_sku_template(temp_filepath, template_type)
        if not parsed_data:
            return jsonify({'error': f'No data extracted using template [{template_type}]. Check the file format.'}), 400

        validated_data, db_online, db_error = validate_rows(parsed_data, template_type)

        summary = {
            'ok':         sum(1 for r in validated_data if r['status'] == 'ok'),
            'update':     sum(1 for r in validated_data if r['status'] == 'update'),
            'conflict':   sum(1 for r in validated_data if r['status'] == 'conflict'),
            'rejected':   sum(1 for r in validated_data if r['status'] == 'rejected'),
            'duplicate':  sum(1 for r in validated_data if r['status'] == 'duplicate'),
            'db_offline': sum(1 for r in validated_data if r['status'] == 'db_offline'),
        }

        logger.info(
            f"[parse_sku_file] user={session.get('sdr_curr_user_username')} "
            f"template={template_type} rows={len(validated_data)} "
            f"db_online={db_online} summary={summary}"
        )

        return jsonify({
            'success':   True,
            'row_count': len(validated_data),
            'summary':   summary,
            'data':      validated_data,
            'db_online': db_online,
            'db_error':  db_error,
        }), 200

    except Exception as e:
        logger.error(f"parse_sku_file error: {traceback.format_exc()}")
        return jsonify({'error': f'Failed to process file: {str(e)}'}), 500

    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)


@transactions_bp.route('/api/commit_barcodes', methods=['POST'])
def commit_barcodes():
    """Stages 5–6: Write approved rows to DB + write audit log."""
    if not session.get('sdr_loggedin'):
        return jsonify({'error': 'Not authenticated'}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({'error': 'No payload received'}), 400

    rows = payload.get('data', [])
    if not rows:
        return jsonify({'error': 'No data to commit'}), 400

    committed_by = session.get('sdr_curr_user_username', 'unknown')
    updateable   = [r for r in rows if r.get('status') == 'update']

    if not updateable:
        return jsonify({
            'success':   True,
            'committed': 0,
            'skipped':   len(rows),
            'errors':    [],
            'message':   'No rows with status=update — nothing to commit'
        }), 200

    result = commit_rows_to_db(rows, committed_by)

    logger.info(
        f"[commit_barcodes] user={committed_by} "
        f"committed={result['committed']} skipped={result['skipped']} errors={len(result['errors'])}"
    )

    return jsonify(result), 200 if result['success'] else 207


@transactions_bp.route('/api/barcode_audit_log', methods=['GET'])
def get_audit_log():
    """Returns the last 200 entries from barcode_audit_log."""
    if not session.get('sdr_loggedin'):
        return jsonify({'error': 'Not authenticated'}), 401

    conn, cursor = _get_barcodes_conn()
    if conn is None:
        return jsonify({'error': 'Cannot connect to Barcodes DB'}), 500

    try:
        cursor.execute("""
            SELECT TOP 200
                id, committed_at, committed_by,
                item_code, barcode_new, action, notes
            FROM dbo.barcode_audit_log
            ORDER BY committed_at DESC
        """)
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

        for r in rows:
            if r.get('committed_at') and hasattr(r['committed_at'], 'strftime'):
                r['committed_at'] = r['committed_at'].strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({'success': True, 'count': len(rows), 'data': rows}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# --- PROGRESS TRACKING HELPER (FILE BASED) ---
# We use files instead of variables so this works even if the server uses multiple worker processes.

PROGRESS_DIR = os.path.join(os.getcwd(), 'temp_progress')
os.makedirs(PROGRESS_DIR, exist_ok=True)

def save_progress(req_id: str, current: int, total: int, status: str) -> None:
    """Write progress state to a JSON file readable by all worker processes."""
    try:
        file_path = os.path.join(PROGRESS_DIR, f"{req_id}.json")
        data = {"current": current, "total": total, "status": status}
        with open(file_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to write progress: {e}")

def get_progress_data(req_id: str) -> dict:
    """Read progress state from the JSON file written by save_progress."""
    try:
        file_path = os.path.join(PROGRESS_DIR, f"{req_id}.json")
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"current": 0, "total": 0, "status": "Waiting..."}

# --- SHARED UTILITY FUNCTIONS ---

def get_mysql_conn():
    try:
        return mysql.connector.connect(
            host=os.getenv('MYSQL_HOST', 'localhost'),
            user=os.getenv('MYSQL_USER', 'root'),
            password=os.getenv('MYSQL_PASSWORD', ''),
            database=os.getenv('MYSQL_DB', 'myproject'),
        )
    except Exception:
        return None

def _safe_sheet_name(name: str, max_len: int = 31) -> str:
    """Strip characters Excel forbids in sheet names and truncate to max_len."""
    return re.sub(r'[/\\?*\[\]:]', '', str(name))[:max_len]


def build_image_cache(base_path: str) -> dict:
    """Walk *base_path* and index image files by their first character for fast lookup."""
    cache = {}
    extensions = {'.jpg', '.jpeg', '.png'}
    try:
        if os.path.exists(base_path):
            for root, _, files in os.walk(base_path):
                for filename in files:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in extensions:
                        name_lower = os.path.splitext(filename)[0].lower()
                        full_path = os.path.join(root, filename)
                        first_char = name_lower[0] if name_lower else ''
                        if first_char not in cache:
                            cache[first_char] = []
                        cache[first_char].append((name_lower, full_path))
    except Exception as e:
        logger.error(f"Cache build error: {e}")
    return cache

def find_image_in_cache(cache: dict, item_no: str) -> Optional[str]:
    """Return the full path of the image for *item_no*, or None if not found."""
    item_no_lower = str(item_no).strip().lower()
    if not item_no_lower:
        return None
    first_char = item_no_lower[0]
    bucket = cache.get(first_char, [])
    # Exact match first, then prefix match
    for name, path in bucket:
        if name == item_no_lower:
            return path
    for name, path in bucket:
        if name.startswith(item_no_lower):
            return path
    return None

# --- ROUTES ---

@transactions_bp.route('/progress')
def progress():
    req_id = request.args.get('id', 'default')
    
    def generate():
        while True:
            data = get_progress_data(req_id)
            yield f"data: {json.dumps(data)}\n\n"
            if data["status"] == "Finalizing..." or (data["total"] > 0 and data["current"] >= data["total"]):
                break
            time.sleep(0.5)
                
    return Response(generate(), mimetype='text/event-stream')

@transactions_bp.route('/verify-codes', methods=['POST'])
def verify_codes():
    pc_memo = request.form.get('pc_memo', '').strip().upper()
    sales_code = request.form.get('sales_code', '').strip().upper()
    company_selection = request.form.get('company', '').strip().upper()
    
    is_atcrep = company_selection in ['ATC', 'TPC']
    db_target = 'ATCREP' if is_atcrep else 'NICREP'
    
    if company_selection == 'ATC':
        table_prefix = 'About Time Corporation' 
    elif company_selection == 'TPC':
        table_prefix = 'Transcend Prime Inc'
    else:
        table_prefix = 'Newtrends International Corp_'

    conn = None 
    try:
        # connect to the target database (NICREP or ATCREP)
        conn, cursor, prefix = SQLconnect(db_target, "DSRT")
        if conn is None:
            return jsonify({"success": False, "error": f"Connection to {db_target} Failed"}), 500
            
        check_qry = (f'SELECT COUNT(*) as cnt FROM dbo."{table_prefix}$Sales Price" WITH (NOLOCK) '
                     f'WHERE "Sales Code"=? AND "PC Memo No"=?' )
        cursor.execute(check_qry, (sales_code, pc_memo))
        result = cursor.fetchone()
        
        return jsonify({"success": True, "count": result[0]}) if result and result[0] > 0 else jsonify({"success": False, "error": f"No records found in {db_target}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
    finally:
        if conn: conn.close()

@transactions_bp.route('/get-companies/<chain>')
def get_companies(chain):
    mysql_conn = get_mysql_conn()
    results = []
    if mysql_conn:
        try:
            cursor = mysql_conn.cursor(dictionary=True)
            query = "SELECT company_selection, vendor_code FROM vendor_chain_mappings WHERE chain_name = %s"
            cursor.execute(query, (chain.upper(),))
            results = cursor.fetchall()
        finally:
            mysql_conn.close()

    # If no mappings exist for this chain, return the default placeholders
    if not results:
        return jsonify([
            {"company_selection": "NIC", "vendor_code": None, "is_default": True},
            {"company_selection": "ATC", "vendor_code": None, "is_default": True},
            {"company_selection": "TPC", "vendor_code": None, "is_default": True}
        ])
    
    return jsonify(results)


# --- MAIN CONTROLLER ROUTE ---

@transactions_bp.route('/process-template', methods=['POST'])
def process_template():
    # 1. Get Selections
    chain_selection = request.form.get('chain', '').strip().upper()
    company_selection = request.form.get('company', '').strip().upper()
    pc_memo = request.form.get('pc_memo', '').strip().upper()
    sales_code = request.form.get('sales_code', '').strip().upper()

    req_id = sales_code
    save_progress(req_id, 0, 0, "Initializing...")

    # 2. REDIRECTION LOGIC
    if company_selection in ['ATC', 'TPC']:
        logger.info(f"Redirecting to ATC/TPC logic for company: {company_selection}")
        dummy_progress = {} 
        return process_atcrep_template(
            chain_selection, company_selection, pc_memo, sales_code, 
            SQLconnect, get_mysql_conn, build_image_cache, 
            find_image_in_cache, NETWORK_IMAGE_PATH, dummy_progress
        )

    # 3. NIC SCRIPT LOGIC
    save_progress(req_id, 0, 0, "Accessing NICREP...")
    conn = None
    try:
        conn, cursor, prefix = SQLconnect('NICREP', "DSRT")
        if conn is None:
            return jsonify({"error": "Database Connection Failed"}), 500

        # --- DYNAMIC DISCOUNT LEVEL FETCH (Sales Price Table) ---
        try:
            price_qry = (
                'SELECT "Item No_", "SRP", "Price_Discount" FROM ('
                '  SELECT "Item No_", "Unit Price" AS SRP, "Discount Level" AS "Price_Discount", '
                '  ROW_NUMBER() OVER (PARTITION BY "Item No_" ORDER BY "Starting Date" DESC) as RowNum '
                '  FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
                '  WHERE "Sales Code"=? AND "PC Memo No"=?'
                ') t WHERE RowNum = 1'
            )
            prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])
        except Exception as e:
            price_qry = (
                'SELECT "Item No_", "SRP" FROM ('
                '  SELECT "Item No_", "Unit Price" AS SRP, '
                '  ROW_NUMBER() OVER (PARTITION BY "Item No_" ORDER BY "Starting Date" DESC) as RowNum '
                '  FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
                '  WHERE "Sales Code"=? AND "PC Memo No"=?'
                ') t WHERE RowNum = 1'
            )
            prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])

        if prices_df.empty:
            return jsonify({"error": "No records found in Navision for the provided codes."}), 404

        item_list = prices_df['Item No_'].tolist()
        total_items_count = len(item_list)
        
        save_progress(req_id, 0, total_items_count, f"Found {total_items_count} items. Starting Retrieval...")

        # --- CHUNKING LOGIC (5K+ Items Support) ---
        chunk_size = 2000
        items_dfs = []
        attr_dfs = []

        # Iterate through item list in chunks to prevent query overflow
        for i in range(0, len(item_list), chunk_size):
            chunk = item_list[i:i + chunk_size]
            placeholders = ', '.join(['?'] * len(chunk))
            
            # Update Progress File
            save_progress(req_id, i, total_items_count, f"Retrieving item details... ({i}/{total_items_count})")

            # A. Fetch Base Item Data
            try:
                item_qry = (f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                            f'"Vendor Item No_" AS "Style_Stockcode", "Net Weight", "Gross Weight", '
                            f'"Base Unit of Measure" AS "Unit_of_Measure", '
                            f'"Dial Color", "Case _Frame Size", "Gender", "Case_Frame Material" AS "Material", "Item Category Code", "Discount Level" AS "Item_Discount" '
                            f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                            f'WHERE "No_" IN ({placeholders})')
                chunk_df = pd.read_sql(item_qry, conn, params=chunk)
            except Exception:
                item_qry = (f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                            f'"Vendor Item No_" AS "Style_Stockcode", "Net Weight", "Gross Weight", '
                            f'"Base Unit of Measure" AS "Unit_of_Measure", '
                            f'"Item Category Code" '
                            f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                            f'WHERE "No_" IN ({placeholders})')
                chunk_df = pd.read_sql(item_qry, conn, params=chunk)
            
            items_dfs.append(chunk_df)

            attr_qry = f'''
                SELECT a."No_", b."Name" as "Attribute", c."Value" 
                FROM dbo."Newtrends International Corp_$Item Attribute Value Mapping" a WITH (NOLOCK)
                LEFT JOIN dbo."Newtrends International Corp_$Item Attribute" b ON a."Item Attribute ID" = b."ID"
                LEFT JOIN dbo."Newtrends International Corp_$Item Attribute Value" c ON a."Item Attribute ID" = c."Attribute ID" 
                     AND a."Item Attribute Value ID" = c."ID"
                WHERE a."Table ID" = 27 AND a."No_" IN ({placeholders})
            '''
            try:
                chunk_attrs = pd.read_sql(attr_qry, conn, params=chunk)
                attr_dfs.append(chunk_attrs)
            except Exception as e:
                logger.error(f"Attribute fetch failed for chunk {i}: {e}")
            
            # Tiny sleep to let other threads breathe DO NOT REMOVE
            time.sleep(0.01)

        # --- DATA RECONSTRUCTION ---
        if items_dfs:
            items_df = pd.concat(items_dfs, ignore_index=True)
        else:
            items_df = pd.DataFrame()

        if attr_dfs:
            attr_df = pd.concat(attr_dfs, ignore_index=True)
            if not attr_df.empty:
                # Pivot the attributes to create columns
                pivoted = attr_df.pivot(index='No_', columns='Attribute', values='Value').reset_index()
                
                # Map Attribute names to match existing Excel logic columns
                rename_map = {
                    'Pricepoint': 'Point_Power', 
                    'Dial Color': 'Dial Color',
                    'Case _Frame Size': 'Case _Frame Size',
                    'Gender': 'Gender'
                }
                pivoted = pivoted.rename(columns=rename_map)
                items_df = pd.merge(items_df, pivoted, how='left', left_on='Item No_', right_on='No_')

        # Ensure all columns exist for the Excel mapping to avoid KeyErrors
        for col in ['Point_Power', 'Dial Color', 'Case _Frame Size', 'Gender', 'Net Weight', 'Gross Weight', 'Item_Discount']:
            if col not in items_df.columns:
                items_df[col] = ""

        merged_df = pd.merge(items_df, prices_df, on="Item No_")

        # Dynamically map Discount Level regardless of which table successfully extracted it
        if 'Price_Discount' in merged_df.columns and not merged_df['Price_Discount'].isna().all():
            merged_df['Discount Level'] = merged_df['Price_Discount']
        elif 'Item_Discount' in merged_df.columns and not merged_df['Item_Discount'].isna().all():
            merged_df['Discount Level'] = merged_df['Item_Discount']
        else:
            merged_df['Discount Level'] = ""

        # --- DYNAMIC VENDOR & BRAND LOOKUP (MYSQL) ---
        mysql_conn = get_mysql_conn()
        vendor_code, dynamic_mfg_no = "000000", ""
        if mysql_conn:
             try:
                v_cursor = mysql_conn.cursor()
                v_cursor.execute("SELECT vendor_code FROM vendor_chain_mappings WHERE chain_name = %s AND company_selection = %s", (chain_selection, company_selection))
                v_res = v_cursor.fetchone()
                if v_res: 
                    vendor_code = str(v_res[0])
                    v_cursor.execute("SELECT mfg_part_no FROM vendors_rds WHERE vendor_code = %s", (vendor_code,))
                    mfg_res = v_cursor.fetchone()
                    if mfg_res: dynamic_mfg_no = str(mfg_res[0])
             finally:
                 mysql_conn.close()

        # --- 4. DATA MAPPING ---
        time_now = datetime.now()
        zip_date = time_now.strftime('%m%d%Y')

        # Define filenames and zip names per chain
        if chain_selection == "RDS":
            filename_base = f'RDS {company_selection} {time_now.strftime("%m%d%Y")}'
            final_zip_name = f"RDS{zip_date}.zip"
        elif chain_selection == "RUSTANS":
            filename_base = f'RUSTANS {time_now.strftime("%m%d%Y")} {company_selection}'
            final_zip_name = f"RUSTANS{zip_date}.zip"
        elif chain_selection == "GCAP":
            filename_base = f'GCAP {company_selection} {time_now.strftime("%m%d%Y")}' 
            final_zip_name = f"GCAP{zip_date}.zip"
        elif chain_selection == "KCC":
            filename_base = f'KCC SKU {time_now.strftime("%m%d%Y")} {company_selection}'
            final_zip_name = f"KCC{zip_date}.zip"
        elif chain_selection in ["GGRAND", "ALTURAS"]:
            filename_base = f'{chain_selection} {company_selection} {time_now.strftime("%m%d%Y")}'
            final_zip_name = f"{chain_selection}{zip_date}.zip"
        elif chain_selection == "METRO":
            filename_base = f'{chain_selection} {company_selection} {time_now.strftime("%m%d%Y")}'
            final_zip_name = f"{chain_selection}{zip_date}.zip"
        elif chain_selection in ["WATSONS", "WATSONS ONLINE"]:
            sm_ts = time_now.strftime('%m%d%H%M')
            filename_base = f"SC{vendor_code}_DEPT_CLASS_{sm_ts}"
            chain_prefix = "WATSONS_ONLINE" if chain_selection == "WATSONS ONLINE" else "WATSONS"
            final_zip_name = f"{chain_prefix}{zip_date}.zip"
        else:
            # Temporary savefile, will be zipped later and adjusted to required store chain format
            sm_ts = time_now.strftime('%m%d%H%M')
            filename_base = f"SC{vendor_code}_DEPT_CLASS_{sm_ts}"
            final_zip_name = f"SM{zip_date}.zip"

        if chain_selection == "RDS":
            # PAGE 1
            merged_df['SKU Number'] = ""; merged_df['SKU Number with check digit'] = ""; merged_df['Sku Number'] = ""
            merged_df['Item Description'] = merged_df['Description'].fillna('').str[:30]
            merged_df['Short name'] = merged_df['Description'].fillna('').str[:10]
            merged_df['Item Status'] = "A"; merged_df['Buyer'] = "B92"; merged_df['W/SCD 5% DISC'] = "N"; merged_df['Inventory Grp'] = ""; merged_df['W/PWD 5% DISC'] = "N"
            merged_df['SKU Type'] = ""; merged_df['Merchandiser'] = ""; merged_df['POS Tax Code'] = "V"
            merged_df['Primary Vendor'] = vendor_code; merged_df['Ship Pt'] = ""; merged_df['Manufacturer'] = ""; merged_df['Vendor Part#'] = ""; merged_df['Manufacturer Part#'] = dynamic_mfg_no
            merged_df['Dept'] = ""; merged_df['Sub-Dept'] = ""; merged_df['Class-'] = ""; merged_df['Sub-Class'] = ""
            # PAGE 2
            merged_df['Product Code'] = ""; merged_df['TYPE'] = ""; merged_df['Primary Buy UPC'] = ""; merged_df['Saleable UPC'] = ""
            # PAGE 3
            merged_df['Competitive Priced'] = ""; merged_df['Display on Web'] = ""; merged_df['Competitive Price'] = ""; merged_df['POS Price Prompt'] = ""
            merged_df['Original Price'] = merged_df['SRP'].fillna(0).map('{:.2f}'.format)
            merged_df['Prevent POS Download'] = "N"; merged_df['Next Regular Retail'] = ""; merged_df['Effective'] = ""; merged_df['Current Vendor Cost'] = ""
            merged_df['Buying U/M'] = "PCS"; merged_df['Selling U/M'] = "PCS"; merged_df['Standard Pack'] = "-"; merged_df['Minimum (Inner) Pack'] = "-"
            # PAGE 4
            merged_df['Coordinate Group'] = "RDS"; merged_df['Super Brand'] = ""; merged_df['Brand_Maint'] = merged_df['Brand'].fillna('')
            merged_df['Buy Code(C/S)'] = "S"; merged_df['Season'] = "NA"; merged_df['Set Code'] = "-"; merged_df['Mfg. No.'] = "-"; merged_df['Age Code'] = "-"; merged_df['Label'] = "-"; merged_df['Origin'] = "-"; merged_df['Tag'] = "-"; merged_df['Fair Event'] = "-"; merged_df['Blank Field'] = "-"; merged_df['Price Point'] = merged_df['Point_Power'].fillna(''); merged_df['Merchandise Flag'] = "-"; merged_df['Hold Wholesale Order'] = "N"; merged_df['Size'] = merged_df['Case _Frame Size'].fillna(''); merged_df['Substitute SKU'] = ""; merged_df['Core SKU'] = ""; merged_df['Replacement SKU'] = ""
            # PAGE 5
            merged_df['Replenishment Code'] = "0"; merged_df['Sales $ (Blank)'] = ""; merged_df['Distribution Method'] = ""; merged_df['Sales Units'] = ""; merged_df['Rpl Start Date'] = ""
            merged_df['Gross Margin'] = ""; merged_df['Rpl End Date'] = ""; merged_df['User Defined'] = ""; merged_df['Avg. Model Stock'] = ""; merged_df['Avg. Order at'] = ""; merged_df['Maximum Stock'] = ""; merged_df['Display Minimum'] = ""; merged_df['Stock in Mult. of'] = ""; merged_df['Minimum Rpl Qty'] = "-"; merged_df['Item Profile'] = ""; merged_df['Hold Order'] = "N"; merged_df['Plan Lead Time'] = ""
            # PAGE 6
            merged_df['Item Weight'] = merged_df['Gross Weight']; merged_df['Item Length'] = ""; merged_df['Width'] = ""; merged_df['Height'] = ""; merged_df['Item Cube'] = ""; merged_df['Pallet Tie'] = ""; merged_df['Pallet High'] = ""; merged_df['Container Type'] = ""; merged_df['Container Multiple'] = ""
            # PAGE 7
            merged_df['Regular Label Type'] = ""; merged_df['Ad Label Type'] = ""; merged_df['Regular Ticket  Type'] = ""; merged_df['Ad Ticket Type'] = ""; merged_df['Tickets per Item'] = ""; merged_df['Is Sign Age Required'] = "N"
            # PAGE 8
            merged_df['Commercial Inv Product'] = ""; merged_df['Selling Unit Weight'] = merged_df['Net Weight']; merged_df['Descriptor'] = ""; merged_df['Derived Description'] = ""; merged_df['12 Character'] = ""; merged_df['15 Character'] = ""; merged_df['18 Character'] = ""; merged_df['21 Character'] = ""; merged_df['20 Character'] = ""; merged_df['Shelf Label'] = ""; merged_df['Blank Field'] = ""; merged_df['Color'] = merged_df['Dial Color'].fillna(''); merged_df['Size_P8'] = merged_df['Case _Frame Size'].fillna(''); merged_df['Dimension'] = ""

            p1 = ['SKU Number', 'SKU Number with check digit', 'Sku Number', 'Item Description', 'Short name', 'Item Status', 'Buyer', 'W/SCD 5% DISC', 'Inventory Grp', 'W/PWD 5% DISC', 'SKU Type', 'Merchandiser', 'POS Tax Code', 'Primary Vendor', 'Ship Pt', 'Manufacturer', 'Vendor Part#', 'Manufacturer Part#', 'Dept', 'Sub-Dept', 'Class-', 'Sub-Class']
            p2 = ['Product Code', 'TYPE', 'Primary Buy UPC', 'Saleable UPC']
            p3 = ['Competitive Priced', 'Display on Web', 'Competitive Price', 'POS Price Prompt', 'Original Price', 'Prevent POS Download', 'Next Regular Retail', 'Effective', 'Current Vendor Cost', 'Buying U/M', 'Selling U/M', 'Standard Pack', 'Minimum (Inner) Pack']
            p4 = ['Coordinate Group', 'Super Brand', 'Brand_Maint', 'Buy Code(C/S)', 'Season', 'Set Code', 'Mfg. No.', 'Age Code', 'Label', 'Origin', 'Tag', 'Fair Event', 'Blank Field', 'Price Point', 'Merchandise Flag', 'Hold Wholesale Order', 'Size', 'Substitute SKU', 'Core SKU', 'Replacement SKU']
            p5 = ['Replenishment Code', 'Sales $ (Blank)', 'Distribution Method', 'Sales Units', 'Rpl Start Date', 'Gross Margin', 'Rpl End Date', 'User Defined', 'Avg. Model Stock', 'Avg. Order at', 'Maximum Stock', 'Display Minimum', 'Stock in Mult. of', 'Minimum Rpl Qty', 'Item Profile', 'Hold Order', 'Plan Lead Time']
            p6 = ['Item Weight', 'Item Length', 'Width', 'Height', 'Item Cube', 'Pallet Tie', 'Pallet High', 'Container Type', 'Container Multiple']
            p7 = ['Regular Label Type', 'Ad Label Type', 'Regular Ticket  Type', 'Ad Ticket Type', 'Tickets per Item', 'Is Sign Age Required']
            p8 = ['Commercial Inv Product', 'Selling Unit Weight', 'Descriptor', 'Derived Description', '12 Character', '15 Character', '18 Character', '21 Character', '20 Character', 'Shelf Label', 'Blank Field', 'Color', 'Size_P8', 'Dimension']

            rds_sections = [
                (p1, 'PAGE 1 - Item Base Data Maintenance', '#BDD7EE'), (p2, 'PAGE 2 - UPC Maintenance', '#E2EFDA'),
                (p3, 'PAGE 3 - Item Cost and Price Maintenance', '#FFF2CC'), (p4, 'PAGE 4 - Item Code Maintenance', '#EAD1DC'),
                (p5, 'PAGE 5 - Item Replenishment Maintenance', '#FCE4D6'), (p6, 'PAGE 6 - Physical Dimension Maintenance', '#D9E1F2'),
                (p7, 'PAGE 7 - Label, Tag, and Ticket Maintenance', '#F2F2F2'), (p8, 'PAGE 8 - Item Descriptions Maintenance', '#E7E6E6')
            ]
            final_layout = []
            for idx, (group, _, _) in enumerate(rds_sections):
                final_layout.extend(group)
                if idx < len(rds_sections) - 1:
                    gap_col = f"GAP_{idx}"
                    merged_df[gap_col] = ""
                    final_layout.append(gap_col)
            
            final_cols, sheet_name_val, header_row_idx, data_start_row = final_layout, "TEMPLATE", 1, 2

        elif chain_selection == "RUSTANS":
            merged_df['VENDOR ITEM CODE'] = merged_df['Item No_']; merged_df['VENDOR CODE'] = vendor_code
            desc_str = (merged_df['Description'].fillna('') + " " + merged_df['Dial Color'].fillna('') + " " + merged_df['Style_Stockcode'].fillna('') + " " + merged_df['Brand'].fillna('')).str.strip()
            merged_df['PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)'] = desc_str.str[:30]
            merged_df['PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)'] = merged_df['Description'].fillna('').str[:10]
            merged_df['PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)'] = desc_str.str[:50]
            merged_df['RETAIL PRICE'] = merged_df['SRP'].fillna(0).apply(lambda x: '{:.2f}'.format(x))
            merged_df['SET / PC'] = "PC"
            merged_df['MATERIAL'] = merged_df['Material'].fillna('') if 'Material' in merged_df.columns else ""
            merged_df['Gender'] = merged_df['Gender'].fillna('') if 'Gender' in merged_df.columns else ""
            merged_df['Dial Color'] = merged_df['Dial Color'].fillna('') if 'Dial Color' in merged_df.columns else ""
            merged_df['Case _Frame Size'] = merged_df['Case _Frame Size'].fillna('') if 'Case _Frame Size' in merged_df.columns else ""

            #blank array
            for col in ['RCC SKU', 'IMAGE', 'BRAND CODE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'SIZE RUN', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 'LINK TO HI-RES IMAGE']: 
                merged_df[col] = ""
                
            final_cols = ['RCC SKU', 'IMAGE', 'VENDOR ITEM CODE', 'PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)', 'PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)', 'PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)', 'VENDOR CODE', 'BRAND CODE', 'RETAIL PRICE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'Dial Color', 'SIZE RUN', 'Case _Frame Size', 'SET / PC', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 'MATERIAL', 'LINK TO HI-RES IMAGE', 'Gender']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGE', "Rustans Template", 14, 15
            
        elif chain_selection == "GCAP":
            merged_df['brand'] = merged_df['Brand'].fillna('')
            merged_df['item code'] = merged_df['Item No_']
            
            #Promo Category Logic (@ or # means PROMO)
            merged_df['promo category'] = merged_df['Description'].fillna('').apply(
                lambda x: "PROMO ITEM" if "@" in str(x) or "#" in str(x) else "REGULAR ITEM"
            )

            # 3. Item Category Abbreviation Logic
            cat_abbrevs = {
                "NON": "NON-MERCHANDISE",
                "OTH": "OTHERS",
                "PRM": "PROMO",
                "PRT": "PARTS",
                "ACC": "ACCESSORIES",
                "WTC": "WATCHES",
                "SKN": "SKIN CARE",
                "FRG": "FRAGRANCE"
            }
            
            def abbreviate_category(val):
                if not val: return ""
                clean_val = str(val).strip().upper()
                return cat_abbrevs.get(clean_val, val) 

            merged_df['item category'] = merged_df['Item Category Code'].apply(abbreviate_category)
            merged_df['price'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            merged_df['description'] = (
                merged_df['Description'].fillna('').astype(str) + " " + 
                merged_df['item code'].astype(str) + " " + 
                merged_df['price'].astype(str)
            ).str.strip()
            
            final_cols = ['brand', 'item code', 'promo category', 'item category', 'description', 'price']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = None, "GCAP Template", 0, 1

        elif chain_selection == "KCC":
            merged_df['SKU'] = merged_df['Item No_']
            merged_df['BARCODE'] = "" 
            merged_df['ITEM CODE/STOCK#'] = merged_df['Style_Stockcode'].fillna('')
            merged_df['BRAND'] = merged_df['Brand'].fillna('')
            merged_df['DESCRIPTION'] = merged_df['Description'].fillna('')
            merged_df['REGULAR PRICE'] = pd.to_numeric(merged_df['Point_Power'], errors='coerce').fillna(0).map('{:,.2f}'.format)
            merged_df['MARKDOWN PRICE'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            merged_df['SPECIFICATION'] = (merged_df['Dial Color'].fillna('') + " " + merged_df['Case _Frame Size'].fillna('')).str.strip()
            merged_df['SAMPLE IMAGE'] = ""
            merged_df['PRICE CATEGORY'] = "SALE ITEM"
            merged_df['DISCOUNT LEVEL'] = merged_df['Discount Level'].fillna('')
            
            final_cols = [
                'SKU', 'BARCODE', 'ITEM CODE/STOCK#', 'BRAND', 'DESCRIPTION', 
                'REGULAR PRICE', 'MARKDOWN PRICE', 'SPECIFICATION', 'SAMPLE IMAGE', 
                'PRICE CATEGORY', 'DISCOUNT LEVEL'
            ] 
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'SAMPLE IMAGE', "Sheet1", 5, 6

        elif chain_selection in ["GGRAND", "ALTURAS"]:

            cat_abbrevs = {
                "NON": "NON-MERCHANDISE",
                "OTH": "OTHERS",
                "PRM": "PROMO",
                "PRT": "PARTS",
                "ACC": "ACCESSORIES",
                "WTC": "WATCHES",
                "SKN": "SKIN CARE",
                "FRG": "FRAGRANCE"
            }
            
            def abbreviate_category(val):
                if not val: return ""
                clean_val = str(val).strip().upper()
                return cat_abbrevs.get(clean_val, val) 

            merged_df['BRAND'] = merged_df['Brand'].fillna('')
            merged_df['PROMO CATEGORY'] = merged_df['Description'].fillna('').apply(
                lambda x: "PROMO ITEM" if "@" in str(x) or "#" in str(x) else "SALE ITEM"
            )
            
            # Generate ITEM CATEGORY and PRICE first for concatenation
            merged_df['ITEM CATEGORY'] = merged_df['Item Category Code'].apply(abbreviate_category)
            merged_df['PRICE'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            
            # Concatenate Description, Price, and Item Category with safe typecasting
            merged_df['DESCRIPTION'] = (
                merged_df['Description'].fillna('').astype(str) + " " + 
                merged_df['PRICE'].astype(str) + " " + 
                merged_df['ITEM CATEGORY'].astype(str)
            ).str.strip()
            
            merged_df['SKU'] = ""
            merged_df['BARCODE'] = ""
            final_cols = ['BRAND', 'PROMO CATEGORY', 'ITEM CATEGORY', 'DESCRIPTION', 'PRICE', 'SKU', 'BARCODE']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = None, f"{chain_selection.title()} Template", 2, 3

        elif chain_selection in ["WATSONS", "WATSONS ONLINE"]:
            safe_color = merged_df['Dial Color'].fillna('') if 'Dial Color' in merged_df.columns else ""
            safe_size = merged_df['Case _Frame Size'].fillna('') if 'Case _Frame Size' in merged_df.columns else ""
            safe_brand = merged_df['Brand'].fillna('')
            safe_desc = merged_df['Description'].fillna('')
            safe_style = merged_df['Style_Stockcode'].fillna('')
            
            merged_df['COLOR'] = safe_color
            merged_df['SIZES'] = safe_size
            
            raw_desc = (safe_brand + " " + safe_desc + " " + safe_color + " " + safe_size + " " + safe_style)
            merged_df['DESCRIPTION'] = raw_desc.str.replace(r'[^a-zA-Z0-9\s]', '', regex=True).str.replace(r'\s+', ' ', regex=True).str.strip().str[:50]
            
            merged_df['SRP'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            merged_df['EXP_DEL_MONTH'] = (time_now + timedelta(days=30)).strftime('%m/%d/%Y')
            merged_df['SOURCE_MARKED'] = ""; merged_df['REMARKS'] = ""
            merged_df['ONLINE ITEMS'] = "YES" if chain_selection == "WATSONS ONLINE" else "NO"
            
            merged_df['PACKAGE WEIGHT IN KG'] = merged_df['Gross Weight']; merged_df['PRODUCT WEIGHT IN KG'] = merged_df['Net Weight']
            
            for d in ['PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM']: 
                merged_df[d] = "-"
                
            merged_df['IMAGES'] = ""
            
            final_cols = ['DESCRIPTION', 'COLOR', 'SIZES', 'Style_Stockcode', 'SOURCE_MARKED', 'SRP', 'Unit_of_Measure', 'EXP_DEL_MONTH', 'REMARKS', 'IMAGES', 'ONLINE ITEMS', 'PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 'PACKAGE WEIGHT IN KG', 'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM', 'PRODUCT WEIGHT IN KG']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGES', "Template", 0, 1

        elif chain_selection == "METRO":
            # [METRO NEW ITEM SAMPLE SHEET MAPPING]
            merged_df['NO'] = range(1, len(merged_df) + 1)
            merged_df['PRODUCT IMAGE'] = ""
            merged_df['DEPT'] = "5926"
            merged_df['CLASS'] = "1"
            merged_df['SUBCLASS'] = "1"
            merged_df['EAN-13'] = ""
            merged_df['BRAND NAME'] = merged_df['Brand'].fillna('')
            merged_df['ITEM DESCRIPTION'] = merged_df['Description'].fillna('')
            merged_df['STOCK/ PRODUCT CODE'] = merged_df['Style_Stockcode'].fillna('')
            
            merged_df['COLOR'] = merged_df['Dial Color'].fillna('') if 'Dial Color' in merged_df.columns else ""
            merged_df['SIZE'] = merged_df['Case _Frame Size'].fillna('') if 'Case _Frame Size' in merged_df.columns else ""
            merged_df['MATERIAL/FABRIC'] = merged_df['Material'].fillna('') if 'Material' in merged_df.columns else ""
            
            merged_df['REGULAR PRICE'] = pd.to_numeric(merged_df['SRP'], errors='coerce').fillna(0)
            merged_df['SALE PRICE'] = ""
            merged_df['STOCK AVAILABILITY'] = "FEBRUARY ONWARDS"
            
            for i in range(27):
                merged_df[f'Store_{i}'] = ""
                
            merged_df['TOTAL QTY'] = ""
            merged_df['APPROVED'] = ""
            merged_df['DISAPPROVED'] = ""
            merged_df['SKU'] = ""
            merged_df['UPC'] = ""
            merged_df['MDSG REMARKS'] = ""
            
            final_cols = ['NO', 'PRODUCT IMAGE', 'DEPT', 'CLASS', 'SUBCLASS', 'EAN-13', 'BRAND NAME', 'ITEM DESCRIPTION', 'STOCK/ PRODUCT CODE', 'COLOR', 'SIZE', 'MATERIAL/FABRIC', 'REGULAR PRICE', 'SALE PRICE', 'STOCK AVAILABILITY'] + [f'Store_{i}' for i in range(27)] + ['TOTAL QTY', 'APPROVED', 'DISAPPROVED', 'SKU', 'UPC', 'MDSG REMARKS']
            
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'PRODUCT IMAGE', "New Item Sample Sheet", 6, 8

        else:
            # [SM / Default Logic]
            safe_color = merged_df['Dial Color'].fillna('') if 'Dial Color' in merged_df.columns else ""
            safe_size = merged_df['Case _Frame Size'].fillna('') if 'Case _Frame Size' in merged_df.columns else ""
            safe_brand = merged_df['Brand'].fillna('')
            safe_desc = merged_df['Description'].fillna('')
            safe_style = merged_df['Style_Stockcode'].fillna('')
            
            merged_df['COLOR'] = safe_color
            merged_df['SIZES'] = safe_size
            
            raw_desc = (safe_brand + " " + safe_desc + " " + safe_color + " " + safe_size + " " + safe_style)
            merged_df['DESCRIPTION'] = raw_desc.str.replace(r'[^a-zA-Z0-9\s]', '', regex=True).str.replace(r'\s+', ' ', regex=True).str.strip().str[:50]
            
            merged_df['SRP'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            merged_df['EXP_DEL_MONTH'] = (time_now + timedelta(days=30)).strftime('%m/%d/%Y')
            merged_df['SOURCE_MARKED'] = ""; merged_df['REMARKS'] = ""; merged_df['ONLINE ITEMS'] = "NO"
            merged_df['PACKAGE WEIGHT IN KG'] = merged_df['Gross Weight']; merged_df['PRODUCT WEIGHT IN KG'] = merged_df['Net Weight']
            
            for d in ['PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM']: 
                merged_df[d] = "-"
                
            merged_df['IMAGES'] = ""
            
            final_cols = ['DESCRIPTION', 'COLOR', 'SIZES', 'Style_Stockcode', 'SOURCE_MARKED', 'SRP', 'Unit_of_Measure', 'EXP_DEL_MONTH', 'REMARKS', 'IMAGES', 'ONLINE ITEMS', 'PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 'PACKAGE WEIGHT IN KG', 'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM', 'PRODUCT WEIGHT IN KG']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGES', "Template", 0, 1

        # --- 5. EXCEL GENERATION ---
        output_buffer = io.BytesIO()
        brand_groups = list(merged_df.groupby('Brand'))
        
        save_progress(req_id, 0, len(merged_df), "Initializing Excel Generation...")
        
        images_found_count = 0 
        is_multisheet_mode = (chain_selection == "RUSTANS")
        
        zip_file = None
        global_writer = None
        
        # [FIX] Track used filenames
        used_filenames = set()

        if is_multisheet_mode:
            global_writer = pd.ExcelWriter(output_buffer, engine='xlsxwriter')
        else:
            zip_file = zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED)

        try:
            for brand_name, bucket_df in brand_groups:
                try:
                    # 1. Prepare Filename (Only for Zip mode)
                    filename = ""
                    if not is_multisheet_mode:
                        if chain_selection in ["RDS", "GCAP", "KCC", "GGRAND", "ALTURAS", "METRO"]:
                            filename = f"{filename_base} - {brand_name}.xlsx"
                        else:
                            f_dept, f_class = "0000", "0000"
                            loop_conn = get_mysql_conn()
                            if loop_conn:
                                try:
                                    l_cursor = loop_conn.cursor(dictionary=True)
                                    clean_brand = str(brand_name).strip()
                                    search_term = clean_brand + '%'
                                    qry = """SELECT b.dept_code, b.sub_dept_code, b.class_code, s.subclass_code
                                             FROM brands b LEFT JOIN sub_classes s ON b.product_group = s.product_group
                                             WHERE b.brand_name LIKE %s LIMIT 1"""
                                    l_cursor.execute(qry, (search_term,))
                                    res = l_cursor.fetchone()
                                    if res:
                                        d = res.get('dept_code') or '00'
                                        sd = res.get('sub_dept_code') or '00'
                                        c = res.get('class_code') or '00'
                                        sc = res.get('subclass_code') or '00'
                                        f_dept = f"{d}{sd}"
                                        f_class = f"{c}{sc}"
                                except Exception as db_e: logger.error(f"Loop Lookup Error: {db_e}")
                                finally: loop_conn.close()
                            # Ensure sm_ts is available for SM filename
                            sm_ts = time_now.strftime('%m%d%H%M')
                            filename = f"SC{vendor_code}_{f_dept}_{f_class}_{sm_ts}.xlsx"

                        # Check for Duplicate Filenames
                        if filename in used_filenames:
                            base, ext = os.path.splitext(filename)
                            counter = 1
                            while f"{base}_{counter}{ext}" in used_filenames:
                                counter += 1
                            filename = f"{base}_{counter}{ext}"
                        used_filenames.add(filename)
                    
                    # Update specific progress dict
                    save_progress(req_id, 0, len(merged_df), f"Processing Brand: {brand_name}")
                    
                    # 2. Setup Writer and Sheet Name
                    if is_multisheet_mode:
                        safe_sheet = _safe_sheet_name(brand_name)
                        current_writer = global_writer
                        current_sheet_name = safe_sheet
                        data_start_row = 12 
                    else:
                        excel_output = io.BytesIO()
                        current_writer = pd.ExcelWriter(excel_output, engine='xlsxwriter')
                        current_sheet_name = sheet_name_val
                        _start_row_map = {'METRO': 8, 'RDS': 2, 'KCC': 6, 'GGRAND': 3, 'ALTURAS': 3}
                        data_start_row = _start_row_map.get(chain_selection, 1)

                    # 3. Write Data to Excel
                    bucket_df[final_cols].to_excel(current_writer, sheet_name=current_sheet_name, index=False, startrow=data_start_row, header=False)
                    workbook, worksheet = current_writer.book, current_writer.sheets[current_sheet_name]
                    
                    # 4. [FORMATTING LOGIC]
                    if chain_selection == "RDS":
                        curr_col = 0
                        for idx, (group, title, color) in enumerate(rds_sections):
                            page_hdr_fmt = workbook.add_format({'bold': True, 'bg_color': color, 'border': 1, 'align': 'center', 'font_size': 11})
                            field_hdr_fmt = workbook.add_format({'bold': True, 'bg_color': color, 'border': 1, 'align': 'center', 'font_size': 9})
                            worksheet.merge_range(0, curr_col, 0, curr_col + len(group) - 1, title, page_hdr_fmt)
                            for field in group:
                                display_name = field if field not in ['Size_P8', 'Brand_Maint'] else ('Size' if field=='Size_P8' else 'Brand')
                                worksheet.write(1, curr_col, display_name, field_hdr_fmt)
                                worksheet.set_column(curr_col, curr_col, 18 if 'Description' in field else 13)
                                curr_col += 1
                            if idx < len(rds_sections) - 1:
                                worksheet.set_column(curr_col, curr_col, 2)
                                curr_col += 1
                                
                    elif chain_selection == "RUSTANS":
                        # Rustans custom format
                        
                        # Styles
                        bold_fmt = workbook.add_format({'bold': True})
                        title_fmt = workbook.add_format({'bold': True, 'font_size': 11})
                        
                        # Top Block Info Rustans Corporation
                        worksheet.write(0, 0, "RUSTAN COMMERCIAL CORPORATION", title_fmt)
                        worksheet.write(1, 0, "CONCESSIONAIRE MANAGEMENT DIVISION", bold_fmt)
                        worksheet.write(2, 0, "NEW PRODUCT INFORMATION SHEET (NPIS)", bold_fmt)
                        
                        worksheet.write(4, 0, "DATE:", bold_fmt)
                        worksheet.write(4, 1, datetime.now().strftime("%Y-%m-%d"))
                        worksheet.write(4, 5, "TARGET DELIVERY TO STORES:", bold_fmt)
                        
                        worksheet.write(5, 0, "DIVISION:", bold_fmt)
                        worksheet.write(5, 5, "DELIVERY TO E-COMMERCE WAREHOUSE:", bold_fmt)
                        
                        worksheet.write(6, 0, "COMPANY NAME:", bold_fmt)
                        worksheet.write(6, 1, "NEWTRENDS INTERNATIONAL CORPORATION")
                        
                        worksheet.write(7, 0, "BRAND:", bold_fmt)
                        worksheet.write(7, 1, brand_name)
                        
                        instr_fmt = workbook.add_format({'bold': True, 'bg_color': '#FFFF00', 'border': 1, 'align': 'center'})
                        worksheet.merge_range(10, 0, 10, len(final_cols)-1, "ALL HIGHLIGHTED COLUMNS IN CHART ARE TO BE FILLED UP BY CONCESSIONAIRE", instr_fmt)
                        

                        rustans_header_fmt = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2', 'border': 1, 'align': 'center', 'text_wrap': True, 'font_size': 9})
                        
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(11, col_num, value, rustans_header_fmt)
                            
                            # Column Sizing
                            if value != img_col_name:
                                if "Description" in value: worksheet.set_column(col_num, col_num, 40)
                                elif "RCC SKU" in value: worksheet.set_column(col_num, col_num, 15)
                                elif any(x in value for x in ["Size", "Color", "Price"]): worksheet.set_column(col_num, col_num, 12)
                                else: worksheet.set_column(col_num, col_num, 18)

                    elif chain_selection == "GCAP":
                        # Professional Blue Theme for GCAP
                        header_fmt = workbook.add_format({
                            'bold': True, 
                            'bg_color': '#2E75B6', 
                            'font_color': 'white', 
                            'border': 1, 
                            'align': 'center'
                        })
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(0, col_num, value, header_fmt)
                            # Widths: Description=45, Others=15
                            width = 45 if value == 'description' else 15
                            worksheet.set_column(col_num, col_num, width)

                    elif chain_selection == "KCC":
                        title_fmt = workbook.add_format({'bold': True, 'font_size': 11})
                        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1, 'align': 'center'})
                        
                        worksheet.write(0, 0, "KCC MALLS SKU REQUEST FORMAT", title_fmt)
                        worksheet.write(1, 0, f"Supplier's Name: ")
                        worksheet.write(2, 0, f"DATE: {datetime.now().strftime('%m/%d/%Y')}")
                        
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(5, col_num, value, header_fmt)
                            # Custom widths
                            if value == 'description': worksheet.set_column(col_num, col_num, 45)
                            elif value == img_col_name: worksheet.set_column(col_num, col_num, 35)
                            else: worksheet.set_column(col_num, col_num, 18)

                    elif chain_selection in ["GGRAND", "ALTURAS"]:
                        title_fmt = workbook.add_format({'bold': True, 'font_size': 12})
                        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2', 'border': 1, 'align': 'center'})
                        
                        worksheet.write(0, 0, "SKU REQUEST TEMPLATE", title_fmt)
                        
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(2, col_num, value, header_fmt)
                            
                            if value == 'DESCRIPTION': worksheet.set_column(col_num, col_num, 40)
                            elif value in ['BRAND', 'PROMO CATEGORY', 'ITEM CATEGORY']: worksheet.set_column(col_num, col_num, 20)
                            else: worksheet.set_column(col_num, col_num, 15)

                    elif chain_selection == "METRO":
                        bold_fmt = workbook.add_format({'bold': True})
                        hdr_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True, 'font_size': 9})
                        red_hdr_fmt = workbook.add_format({'bold': True, 'bg_color': '#E6B8B7', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True, 'font_size': 9})
                        rotate_hdr_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'font_size': 9, 'rotation': 90})
                        
                        worksheet.write(0, 0, "VENDOR NAME :", bold_fmt)
                        worksheet.write(0, 2, f"[{company_selection}]", bold_fmt)
                        worksheet.write(0, 10, "ITEM CLASSIFICATION (Please check):", bold_fmt)
                        worksheet.write(1, 0, "DEPT/CATEGORY :", bold_fmt)
                        worksheet.write(1, 2, "[Better Accesories]", bold_fmt)
                        worksheet.write(1, 10, "[  x  ] REGULAR ITEM", bold_fmt)
                        worksheet.write(2, 0, "BUYING MONTH/YEAR :", bold_fmt)
                        worksheet.write(2, 2, f"[{time_now.strftime('%m/%Y')}]", bold_fmt)
                        worksheet.write(2, 10, "[     ] PROMOTIONAL/SEASONAL ITEM  | DURATION: __________________", bold_fmt)
                        worksheet.write(4, 0, "To be filled up by Supplier:", bold_fmt)
                        worksheet.write(4, 15, "Initial Quantity Allocation per Store (for Concession Items Only):", bold_fmt)
                        worksheet.write(4, 42, "To be filled up by Metro Gaisano:", bold_fmt)
                        worksheet.write(5, 0, "NEW ITEM DETAILS", bold_fmt)
                        worksheet.write(5, 9, "PRODUCT ATTRIBUTES", bold_fmt)
                        worksheet.write(5, 12, "PRICING", bold_fmt)
                        worksheet.write(5, 14, "REMARKS", bold_fmt)
                        
                        dept_names = ['Colon', 'Mandaue', 'Ayala', 'Legazpi', 'Lucena', 'Market Market', 'Angeles', 'Alabang', 'Danao', 'Bacolod', 'Tacloban', 'Pasig', 'Baybay', 'Catbalogan', 'Imus']
                        hyp_names = ['Toledo', 'Maasin', 'Talisay', 'Lapulapu', 'Colon', 'Mambaling', 'Calbayog', 'Carcar', 'Bogo', 'Naga-Camsur', 'Tagaytay', 'Mactan LG']
                        store_names = dept_names + hyp_names
                        dept_codes = ['2001', '2002', '2093', '2004', '2005', '2006', '2007', '2009', '2015', '2016', '2017', '2018', '2019', '2020', '2223']
                        hyp_codes = ['2008', '2010', '6001', '6003', '6004', '6005', '6006', '6009', '6010', '6013', '2015', ''] 
                        store_codes = dept_codes + hyp_codes
                        
                        worksheet.merge_range(5, 15, 5, 15 + len(dept_names) - 1, "Department Store", bold_fmt)
                        worksheet.merge_range(5, 15 + len(dept_names), 5, 15 + len(store_names) - 1, "Hypermarket", bold_fmt)

                        headers_row6 = ['NO', 'PRODUCT IMAGE', 'HIERARCHY', '', '', '', '', '', '', '', '', '', '', '', '']
                        end_headers6 = ['Total Qty Allocation', 'APPROVED', 'DISAPPROVED', 'ITEM CODES', '', '']
                        row6 = headers_row6 + store_codes + end_headers6
                        
                        headers_row7 = ['', '', 'DEPT', 'CLASS', 'SUBCLASS', 'EAN-13 (if_available)', 'BRAND NAME', 'ITEM DESCRIPTION ', 'STOCK/ PRODUCT CODE', 'COLOR', 'SIZE', 'MATERIAL/FABRIC', 'REGULAR PRICE', 'SALE PRICE (promo item only)', 'STOCK AVAILABILITY']
                        end_headers7 = ['Qty', '', '', 'SKU', 'UPC', 'MDSG REMARKS']
                        row7 = headers_row7 + store_names + end_headers7

                        worksheet.set_row(6, 45)
                        worksheet.set_row(7, 85)
                        for col_num in range(len(row6)):
                            val6 = row6[col_num]
                            val7 = row7[col_num]
                            
                            is_store_col = (col_num >= 15 and col_num < 15 + len(store_names))
                            
                            if val6 == 'ITEM CODES' or val7 in ['SKU', 'UPC', 'MDSG REMARKS']:
                                worksheet.write(6, col_num, val6, red_hdr_fmt)
                                worksheet.write(7, col_num, val7, red_hdr_fmt)
                            elif is_store_col:
                                worksheet.write(6, col_num, val6, hdr_fmt)
                                worksheet.write(7, col_num, val7, rotate_hdr_fmt)
                            else:
                                worksheet.write(6, col_num, val6, hdr_fmt)
                                worksheet.write(7, col_num, val7, hdr_fmt)
                            
                            if col_num == 1: worksheet.set_column(col_num, col_num, 15)
                            elif col_num == 7: worksheet.set_column(col_num, col_num, 35)
                            elif col_num == 8: worksheet.set_column(col_num, col_num, 18)
                            elif is_store_col: worksheet.set_column(col_num, col_num, 5)
                            else: worksheet.set_column(col_num, col_num, 12)
                            
                        worksheet.merge_range(6, 2, 6, 4, "HIERARCHY", hdr_fmt)
                        item_code_start = 15 + len(store_names) + 3 
                        worksheet.merge_range(6, item_code_start, 6, item_code_start + 2, "ITEM CODES", red_hdr_fmt)
                    else:
                        # [SM BLUE THEME]
                        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#BDD7EE', 'border': 1, 'align': 'center'}) # SM Blue
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(0, col_num, value, header_fmt)
                            
                            # SM Sizing
                            if value != img_col_name:
                                if any(x in value for x in ["Desc", "Name", "Description"]):
                                    worksheet.set_column(col_num, col_num, 45)
                                elif "Brand" in value:
                                    worksheet.set_column(col_num, col_num, 20)
                                elif any(x in value for x in ["Size", "Color", "Price", "Cost", "Qty", "Stock", "UPC"]):
                                    worksheet.set_column(col_num, col_num, 13)
                                else:
                                    worksheet.set_column(col_num, col_num, 18)
                    
                    # 5. [IMAGE INSERTION]
                    if chain_selection not in ["RDS", "GCAP"] and img_col_name in final_cols:
                        image_cache = build_image_cache(NETWORK_IMAGE_PATH)
                        img_col_idx = final_cols.index(img_col_name)
                        worksheet.set_column(img_col_idx, img_col_idx, 18) 
                        
                        for i, item_no in enumerate(bucket_df['Item No_']):
                            save_progress(req_id, i, len(bucket_df), f"Inserting Images: {item_no}")
                            
                            row_idx = i + data_start_row
                            worksheet.set_row(row_idx, 90)
                            
                            img_path = find_image_in_cache(image_cache, item_no)
                            if img_path:
                                try:
                                    with Image.open(img_path) as img:
                                        img_resized = img.resize((120, 120), Image.Resampling.LANCZOS)
                                        img_byte_arr = io.BytesIO()
                                        img_resized.save(img_byte_arr, format='PNG')
                                        img_byte_arr.seek(0)
                                        worksheet.insert_image(row_idx, img_col_idx, f"{item_no}.png", {'image_data': img_byte_arr, 'object_position': 1})
                                        images_found_count += 1
                                except Exception:
                                    worksheet.write(row_idx, img_col_idx, 'ERR')
                    
                    # 6. Save (If in Zip Mode)
                    if not is_multisheet_mode:
                        current_writer.close()
                        # FIXED: using getvalue() stops the 'I/O operation on closed file' error
                        zip_file.writestr(filename, excel_output.getvalue())

                except Exception as e: 
                    logger.error(f"Brand bucket failed: {e}")

        except Exception as outer_e:
             logger.error(f"Loop Failure: {outer_e}")
        finally:
             if is_multisheet_mode and global_writer: global_writer.close()
             elif zip_file: zip_file.close()

        # Finalize Progress
        save_progress(req_id, len(merged_df), len(merged_df), "Finalizing...")

        output_buffer.seek(0)
        
        if is_multisheet_mode:
             final_name = f"{filename_base}.xlsx"
             mimetype_val = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
             mimetype_val = 'application/zip'
             if chain_selection in ["RDS", "RUSTANS", "GCAP", "KCC", "GGRAND", "ALTURAS", "METRO"]: final_name = f"{filename_base}.zip"
             
             elif chain_selection in ["WATSONS", "WATSONS ONLINE"]: final_name = final_zip_name
             
             else: final_name = f"SM{datetime.now().strftime('%m%d%Y')}.zip" if not final_zip_name or "SC_TEMP" in final_zip_name else final_zip_name

        response = make_response(send_file(output_buffer, mimetype=mimetype_val, as_attachment=True, download_name=final_name))
        response.headers.update({
            'X-Filename': final_name,
            'X-Total-Items': str(len(merged_df)),
            'X-Images-Found': str(images_found_count),
            'Access-Control-Expose-Headers': 'X-Filename, X-Total-Items, X-Images-Found'
        })
        return response

    except Exception as e:
        logger.error(f"Global Failure: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@transactions_bp.route('/transaction-generator')
def transaction_generator():
    if not session.get('sdr_loggedin'): return render_template('home.html')
    return render_template('transaction_form.html')
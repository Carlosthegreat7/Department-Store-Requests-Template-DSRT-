import io
import json
import logging
import os
import re
import tempfile
import time
import traceback
import zipfile
from datetime import datetime, timedelta
from typing import Optional

import mysql.connector
import openpyxl
import pandas as pd
import pyodbc
from flask import Blueprint, Response, jsonify, make_response, render_template, request, send_file, session
from PIL import Image
from werkzeug.utils import secure_filename

try:
    from .transactions_atc import process_atcrep_template
except ImportError:
    from transactions_atc import process_atcrep_template

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from portal.SQLconnection import SQLconnect
except ImportError:
    SQLconnect = None

transactions_bp = Blueprint('transactions', __name__)

NETWORK_IMAGE_PATH = r'\\mgsvr03\catalog'
PROGRESS_DIR = os.path.join(os.getcwd(), 'temp_progress')
os.makedirs(PROGRESS_DIR, exist_ok=True)


# ==============================================================
# CONSTANTS
# ==============================================================

# Category code → full name mapping (shared by GCAP, GGRAND, ALTURAS)
CAT_ABBREVS = {
    'NON': 'NON-MERCHANDISE',
    'OTH': 'OTHERS',
    'PRM': 'PROMO',
    'PRT': 'PARTS',
    'ACC': 'ACCESSORIES',
    'WTC': 'WATCHES',
    'SKN': 'SKIN CARE',
    'FRG': 'FRAGRANCE',
}

# Add new chains here. Format: 'EXCEL COLUMN HEADER': 'DB_TARGET_KEY'
CHAIN_MAPPINGS = {
    'ALTURAS': {
        'SKU/ITEM CODE': 'ITEM_CODE',
        'BARCODE':       'BARCODE',
        'DESCRIPTION':   'DESC',
        'VENDOR NO.':    'VENDOR',
    },
    'KCC': {
        'BARCODE':   'BARCODE',
        'DESCRIPTION': 'DESC',
        'SKU':       'SKU',
        'ITEM CODE/STOCK#': 'ITEM_CODE',
        'BRAND':     'BRAND_CODE',
    },
    'RDS': {
        'SKU NO.':          'BARCODE',
        'VENDOR PART #':    'ITEM_CODE',
        'ITEM DESCRIPTION': 'DESC',
        'BRAND':            'BRAND_CODE',
    },
    'RUSTANS': {
        'RCC SKU':          'BARCODE',
        'VENDOR ITEM CODE': 'ITEM_CODE',
    },
    'GGRAND': {
        'BRAND':       'BRAND_CODE',
        'DESCRIPTION': 'DESC',
        'SKU':         'ITEM_CODE',   # Mapped to ITEM_CODE for database validation
        'BARCODE':     'BARCODE',
    },
    'GCAP': {
        'BRAND':        'BRAND_CODE',
        'ITEM CODE':    'ITEM_CODE',
        'DESCRIPTION':  'DESC',
        'GCAP BARCODE': 'BARCODE',
    },
    'SM': {
        'ITEM':             'ITEM_CODE',
        'ITEM DESCRIPTION': 'DESC',
        'SM UPC':           'BARCODE',
    },
}


# ==============================================================
# SHARED HELPERS
# ==============================================================

_BLANK_VALUES: frozenset = frozenset(('', 'nan', 'none'))


def _is_blank(val) -> bool:
    """Return True if a value represents an empty/missing cell.

    Accepts any type; converts to lowercase string before checking so callers
    do not have to do `.lower()` themselves (and cannot accidentally pass a
    non-string and get an AttributeError).
    """
    return str(val).strip().lower() in _BLANK_VALUES


def _best_odbc_driver() -> Optional[str]:
    """Return the highest-priority installed SQL Server ODBC driver, or None."""
    drivers = [d for d in pyodbc.drivers() if 'ODBC Driver' in d and 'SQL Server' in d]
    for version in ('18', '17', '13'):
        for d in drivers:
            if version in d:
                return d
    return drivers[0] if drivers else None


def _get_pyodbc_conn(database: str):
    """Open a direct Windows-Auth pyodbc connection to MGSVR14, or return (None, None)."""
    driver = _best_odbc_driver()
    if not driver:
        return None, None
    try:
        conn_str = (
            f"DRIVER={{{driver}}};SERVER=MGSVR14;DATABASE={database};"
            f"Trusted_Connection=yes;"
        )
        if '18' in driver:
            conn_str += 'TrustServerCertificate=yes;'
        conn = pyodbc.connect(conn_str, timeout=5)
        logger.info('DB %s: connected via direct pyodbc', database)
        return conn, conn.cursor()
    except Exception as exc:
        logger.warning('DB %s direct connection failed: %s', database, exc)
        return None, None


def _get_barcodes_conn():
    """Return a (conn, cursor) pair for the Barcodes database."""
    conn, cursor = _get_pyodbc_conn('Barcodes')
    if conn is not None:
        return conn, cursor

    # Fallback via SQLconnect registry
    if SQLconnect:
        try:
            c, cur, _ = SQLconnect('Barcodes', 'DSRT')
            if c is not None:
                return c, cur
        except Exception as exc:
            logger.error('SQLconnect fallback failed: %s', exc)

    return None, None


def _get_dsrt_conn():
    """Return a (conn, cursor) pair for the DSRT database."""
    return _get_pyodbc_conn('DSRT')


def _safe_sheet_name(name: str, max_len: int = 31) -> str:
    """Strip characters Excel forbids in sheet names and truncate to max_len."""
    return re.sub(r'[/\\?*\[\]:]', '', str(name))[:max_len]


def _abbreviate_category(val) -> str:
    """Map a short category code to its full name (shared by GCAP, GGRAND, ALTURAS)."""
    if not val:
        return ''
    return CAT_ABBREVS.get(str(val).strip().upper(), str(val))


def get_mysql_conn():
    """Open a MySQL connection using environment variables, or return None."""
    try:
        return mysql.connector.connect(
            host=os.getenv('MYSQL_HOST', 'localhost'),
            user=os.getenv('MYSQL_USER', 'root'),
            password=os.getenv('MYSQL_PASSWORD', ''),
            database=os.getenv('MYSQL_DB', 'myproject'),
        )
    except Exception:
        return None


# ==============================================================
# PROGRESS TRACKING  (file-based so multi-worker servers work)
# ==============================================================

def save_progress(req_id: str, current: int, total: int, status: str) -> None:
    """Write progress state to a JSON file readable by all worker processes."""
    try:
        path = os.path.join(PROGRESS_DIR, f'{req_id}.json')
        with open(path, 'w') as fh:
            json.dump({'current': current, 'total': total, 'status': status}, fh)
    except Exception as exc:
        logger.error('Failed to write progress: %s', exc)


def get_progress_data(req_id: str) -> dict:
    """Read progress state from the JSON file written by save_progress."""
    try:
        path = os.path.join(PROGRESS_DIR, f'{req_id}.json')
        if os.path.exists(path):
            with open(path) as fh:
                return json.load(fh)
    except Exception:
        pass
    return {'current': 0, 'total': 0, 'status': 'Waiting...'}


# ==============================================================
# IMAGE CACHE
# ==============================================================

def build_image_cache(base_path: str) -> dict:
    """Walk base_path and index image files by their first character for fast lookup."""
    cache: dict = {}
    extensions = {'.jpg', '.jpeg', '.png'}
    try:
        if not os.path.exists(base_path):
            return cache
        for root, _, files in os.walk(base_path):
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in extensions:
                    continue
                name_lower = os.path.splitext(filename)[0].lower()
                full_path = os.path.join(root, filename)
                first_char = name_lower[0] if name_lower else ''
                cache.setdefault(first_char, []).append((name_lower, full_path))
    except Exception as exc:
        logger.error('Cache build error: %s', exc)
    return cache


def find_image_in_cache(cache: dict, item_no: str) -> Optional[str]:
    """Return the full path of the image for item_no, or None if not found."""
    item_lower = str(item_no).strip().lower()
    if not item_lower:
        return None
    bucket = cache.get(item_lower[0], [])
    # Exact match first, then prefix match
    for name, path in bucket:
        if name == item_lower:
            return path
    for name, path in bucket:
        if name.startswith(item_lower):
            return path
    return None


# ==============================================================
# STAGE 1 — TEMPLATE DETECTION
# ==============================================================

def _read_head(file_path: str, nrows: int = 30, header=None, sheet_index: int = 0) -> pd.DataFrame:
    """Read the first nrows of a CSV or Excel file without a header."""
    is_csv = file_path.lower().endswith('.csv')
    common = dict(nrows=nrows, header=header)
    if is_csv:
        return pd.read_csv(file_path, encoding='utf-8', encoding_errors='ignore', **common)
    return pd.read_excel(file_path, sheet_name=sheet_index, **common)


def detect_template_type(file_path: str, sheet_index: int = 0) -> str:
    """Sniff the file and return the best-matching CHAIN_MAPPINGS key, or 'UNKNOWN'."""
    try:
        df_head = _read_head(file_path, nrows=30, sheet_index=sheet_index)
        best_match = 'UNKNOWN'
        max_matches = 0

        for _, row in df_head.iterrows():
            row_vals = [str(v).strip().upper() for v in row.values if pd.notna(v)]
            row_vals_no_spaces = [v.replace(' ', '') for v in row_vals]

            for chain, mapping in CHAIN_MAPPINGS.items():
                matches = 0
                for expected in mapping:
                    expected_clean = expected.upper().replace(' ', '')

                    for val in row_vals:
                        val_clean = val.replace(' ', '')

                        if expected_clean in val_clean or val_clean in expected_clean:
                            matches += 1
                            break
                match_pct = matches / len(mapping) if mapping else 0

                if matches >= 2 and matches > max_matches:
                    max_matches = matches
                    best_match = chain
                elif matches >= 2 and matches == max_matches:
                    current_pct = max_matches / len(CHAIN_MAPPINGS[best_match])
                    if match_pct > current_pct:
                        best_match = chain

        if best_match == 'UNKNOWN':
            df_full = _read_head(file_path, nrows=1, sheet_index=sheet_index)
            if len(df_full.columns) >= 82:
                return 'SM'

        return best_match

    except Exception as exc:
        logger.error('Template detection failed: %s', exc)
        return 'UNKNOWN'


# ==============================================================
# STAGE 2 — PARSE ROWS
# ==============================================================

def _read_all_str(file_path: str, sheet_index: int = 0) -> pd.DataFrame:
    """Read the entire file as strings to preserve leading zeros."""
    is_csv = file_path.lower().endswith('.csv')
    if is_csv:
        return pd.read_csv(
            file_path, header=None, dtype=str,
            encoding='utf-8', encoding_errors='ignore',
        )
    return pd.read_excel(file_path, sheet_name=sheet_index, header=None, dtype=str)


def parse_sku_template(file_path: str, template_type: str, sheet_index: int = 0) -> list:
    """Parse the uploaded file and return a list of row dicts.

    Dispatches to a template-specific deep parser when available,
    otherwise falls back to the generic column-mapping path.
    """
    try:
        if template_type == 'SM':
            return _parse_sm(file_path, sheet_index)
        if template_type == 'RDS':
            return _parse_rds(file_path, sheet_index)
        if template_type == 'RUSTANS':
            return _parse_rustans(file_path, sheet_index)
        if template_type == 'GCAP':
            return _parse_gcap(file_path, sheet_index)
        # All other chains (GGRAND, ALTURAS, KCC, …) use the generic path
        return _parse_generic(file_path, template_type, sheet_index)
    except Exception as exc:
        logger.error('parse_sku_template error [%s]: %s', template_type, exc)
        return []


# ------------------------------------------------------------------
# Deep parser: GCAP
# Direct column mapping: ITEM CODE → ITEM_CODE, GCAP BARCODE → BARCODE
# Also captures BRAND → BRAND_CODE, DESCRIPTION → DESC
# ------------------------------------------------------------------
def _parse_gcap(file_path: str, sheet_index: int = 0) -> list:
    """GCAP template — direct named-column extraction."""
    extracted_data = []
    mapping = CHAIN_MAPPINGS['GCAP']
    df_full = _read_all_str(file_path, sheet_index)

    header_idx = _find_header_row(df_full, mapping)
    if header_idx == -1:
        logger.warning('GCAP: header row not found')
        return extracted_data

    df_full.columns = [
        str(c).strip().upper() if pd.notna(c) else f'UNNAMED_{i}'
        for i, c in enumerate(df_full.iloc[header_idx].values)
    ]
    df_data = df_full.iloc[header_idx + 1:].reset_index(drop=True)
    col_map = _build_col_map(df_data.columns, mapping)

    for _, row in df_data.iterrows():
        item_code = _get_col(row, col_map, 'ITEM_CODE')
        barcode   = _get_col(row, col_map, 'BARCODE')

        # Skip rows with no useful data
        if _is_blank(item_code.lower()) and _is_blank(barcode.lower()):
            continue

        extracted_data.append({
            'ITEM_CODE':  item_code,
            'BARCODE':    barcode,
            'DESC':       _get_col(row, col_map, 'DESC'),
            'BRAND_CODE': _get_col(row, col_map, 'BRAND_CODE'),
        })

    logger.info('GCAP: extracted %d rows', len(extracted_data))
    return extracted_data


# ------------------------------------------------------------------
# Deep parser: SM
# Extracts ITEM_CODE from the last token of the description column.
# Dynamically hunts for the Barcode column to prevent out-of-bounds errors.
# ------------------------------------------------------------------
def _parse_sm(file_path: str, sheet_name=0) -> list:
    """SM template — extracts ITEM_CODE from the last token of the description column."""
    extracted_data = []
    df = _read_all_str(file_path, sheet_name)

    # Defaults for legacy files
    DESC_COL    = 0
    BARCODE_COL = 81

    # 1. SMART COLUMN FINDER (Protects against layout changes)
    # Scans the first 50 rows looking for header keywords
    for row_idx in range(min(50, len(df))):
        row_vals = [str(v).strip().upper() for v in df.iloc[row_idx].values]
        
        if 'SM UPC' in row_vals:
            BARCODE_COL = row_vals.index('SM UPC')

            if 'ITEM DESCRIPTION' in row_vals:
                DESC_COL = row_vals.index('ITEM DESCRIPTION')
            elif 'ITEM' in row_vals:
                DESC_COL = row_vals.index('ITEM')
            elif 'DESCRIPTION' in row_vals:
                DESC_COL = row_vals.index('DESCRIPTION')

            break

        elif 'BARCODE' in row_vals:
            BARCODE_COL = row_vals.index('BARCODE')

            if 'ITEM DESCRIPTION' in row_vals:
                DESC_COL = row_vals.index('ITEM DESCRIPTION')
            elif 'ITEM' in row_vals:
                DESC_COL = row_vals.index('ITEM')
            elif 'DESCRIPTION' in row_vals:
                DESC_COL = row_vals.index('DESCRIPTION')

            break

    _SKIP_DESC = {'nan', 'none', '', 'item', 'item description', 'item desc', 'description'}
    _SKIP_BAR  = {'nan', 'none', '', 'sm upc', 'barcode', 'upc'}

    for _, row in df.iterrows():
        # 2. SAFE ROW LENGTH CHECK
        # Only skips if the row physically isn't wide enough to contain our target columns
        max_needed_index = max(DESC_COL, BARCODE_COL)
        if len(row) <= max_needed_index:
            continue

        raw_desc = str(row.iloc[DESC_COL]).strip()
        raw_bar  = str(row.iloc[BARCODE_COL]).strip()

        if raw_desc.lower() in _SKIP_DESC:
            continue
        if raw_bar.lower() in _SKIP_BAR:
            continue
        if not any(c.isalnum() for c in raw_desc):
            continue

        # 3. YOUR TOKEN LOGIC (Extract Item Code from Description)
        # e.g. "GUESS NEWTRENDS GUESS 0001018 GW0997L3" → "GW0997L3"
        tokens    = raw_desc.split()
        item_code = tokens[-1] if tokens else raw_desc
        desc      = ' '.join(tokens[:-1]) if len(tokens) > 1 else raw_desc

        extracted_data.append({
            'ITEM_CODE': item_code,
            'BARCODE':   raw_bar,
            'DESC':      desc,
        })

    logger.info('SM: extracted %d rows', len(extracted_data))
    return extracted_data

# ------------------------------------------------------------------
# Deep parser: RDS
# Multi-row headers must be skipped.
# SKU NO.       → BARCODE   (RDS's own SKU is stored as the barcode)
# VENDOR PART # → ITEM_CODE (our internal item code)
# ITEM DESCRIPTION → DESC
# BRAND → BRAND_CODE
# ------------------------------------------------------------------
def _parse_rds(file_path: str, sheet_index: int = 0) -> list:
    """RDS template — skips multi-row headers, maps SKU NO. to BARCODE."""
    extracted_data = []
    mapping = CHAIN_MAPPINGS['RDS']
    df_full = _read_all_str(file_path, sheet_index)

    # RDS files often have 2–3 merged header rows before the real column row.
    # _find_header_row scans up to row 50 and picks the best match.
    header_idx = _find_header_row(df_full, mapping)
    if header_idx == -1:
        logger.warning('RDS: header row not found')
        return extracted_data

    df_full.columns = [
        str(c).strip().upper() if pd.notna(c) else f'UNNAMED_{i}'
        for i, c in enumerate(df_full.iloc[header_idx].values)
    ]
    df_data = df_full.iloc[header_idx + 1:].reset_index(drop=True)
    col_map = _build_col_map(df_data.columns, mapping)

    for _, row in df_data.iterrows():
        # RDS mapping: SKU NO. → BARCODE, VENDOR PART # → ITEM_CODE
        barcode   = _get_col(row, col_map, 'BARCODE')    # sourced from SKU NO.
        item_code = _get_col(row, col_map, 'ITEM_CODE')  # sourced from VENDOR PART #

        if _is_blank(barcode) and _is_blank(item_code):
            continue
        # Skip rows that appear to be sub-headers (non-numeric SKU when barcode expected)
        if not any(c.isdigit() for c in barcode) and barcode:
            continue

        extracted_data.append({
            'ITEM_CODE':  item_code,
            'BARCODE':    barcode,
            'DESC':       _get_col(row, col_map, 'DESC'),
            'BRAND_CODE': _get_col(row, col_map, 'BRAND_CODE'),
        })

    logger.info('RDS: extracted %d rows', len(extracted_data))
    return extracted_data


# ------------------------------------------------------------------
# Deep parser: RUSTANS
# Form-based extraction using fixed cell positions.
# The file is structured with labeled cells rather than a flat table.
# Multiple consecutive rows may represent a single item.
# Key cells: RCC SKU → BARCODE, VENDOR ITEM CODE → ITEM_CODE
# ------------------------------------------------------------------
def _parse_rustans(file_path: str, sheet_index: int = 0) -> list:
    """RUSTANS template — form-based fixed-cell extraction with multi-row grouping."""
    extracted_data = []
    mapping = CHAIN_MAPPINGS['RUSTANS']
    df_full = _read_all_str(file_path, sheet_index)

    # Locate the header row to understand column positions
    header_idx = _find_header_row(df_full, mapping)
    if header_idx == -1:
        logger.warning('RUSTANS: header row not found, attempting raw scan')
        # Fallback: scan every cell pair for known labels
        return _parse_rustans_raw_scan(df_full)

    df_full.columns = [
        str(c).strip().upper() if pd.notna(c) else f'UNNAMED_{i}'
        for i, c in enumerate(df_full.iloc[header_idx].values)
    ]
    df_data = df_full.iloc[header_idx + 1:].reset_index(drop=True)
    col_map = _build_col_map(df_data.columns, mapping)

    # Group rows: accumulate values and emit a record when BARCODE is found
    current: dict = {'ITEM_CODE': '', 'BARCODE': ''}
    for _, row in df_data.iterrows():
        barcode   = _get_col(row, col_map, 'BARCODE')
        item_code = _get_col(row, col_map, 'ITEM_CODE')

        if not _is_blank(item_code):
            current['ITEM_CODE'] = item_code
        if not _is_blank(barcode):
            current['BARCODE'] = barcode

        # Emit when we have both fields
        if current['ITEM_CODE'] and current['BARCODE']:
            extracted_data.append(dict(current))
            current = {'ITEM_CODE': '', 'BARCODE': ''}

    logger.info('RUSTANS: extracted %d rows', len(extracted_data))
    return extracted_data


def _parse_rustans_raw_scan(df: pd.DataFrame) -> list:
    """Fallback: walk every cell looking for RUSTANS label/value pairs."""
    extracted_data = []
    rcc_col = vendor_col = None

    for row_idx, row in df.iterrows():
        vals = [str(v).strip().upper() for v in row.values]
        # Find columns by scanning for known header labels
        for col_idx, val in enumerate(vals):
            if 'RCC SKU' in val:
                rcc_col = col_idx
            if 'VENDOR ITEM CODE' in val:
                vendor_col = col_idx

        if rcc_col is not None and vendor_col is not None:
            barcode   = str(row.iloc[rcc_col]).strip()   if len(row) > rcc_col    else ''
            item_code = str(row.iloc[vendor_col]).strip() if len(row) > vendor_col else ''
            if not _is_blank(barcode) and not _is_blank(item_code):
                extracted_data.append({'ITEM_CODE': item_code, 'BARCODE': barcode})

    return extracted_data


# ------------------------------------------------------------------
# Generic fallback parser (GGRAND, ALTURAS, KCC, …)
# ------------------------------------------------------------------
def _parse_generic(file_path: str, template_type: str, sheet_index: int = 0) -> list:
    """Generic column-mapping parser for chains without a custom deep parser."""
    extracted_data = []

    if template_type not in CHAIN_MAPPINGS:
        logger.warning('No mapping found for template: %s', template_type)
        return extracted_data

    mapping = CHAIN_MAPPINGS[template_type]
    df_full = _read_all_str(file_path, sheet_index)

    header_idx = _find_header_row(df_full, mapping)
    if header_idx == -1:
        return extracted_data

    df_full.columns = [
        str(c).strip().upper() if pd.notna(c) else f'UNNAMED_{i}'
        for i, c in enumerate(df_full.iloc[header_idx].values)
    ]
    df_data = df_full.iloc[header_idx + 1:].copy()
    col_map = _build_col_map(df_data.columns, mapping)

    for _, row in df_data.iterrows():
        row_dict = {'ITEM_CODE': '', 'BARCODE': ''}
        has_data = False

        for file_col, target_key in col_map.items():
            val = str(row[file_col]).strip()
            if not _is_blank(val):
                row_dict[target_key] = val
                has_data = True

        # SKU can serve as a fallback ITEM_CODE
        if not row_dict.get('ITEM_CODE') and row_dict.get('SKU'):
            row_dict['ITEM_CODE'] = row_dict['SKU']

        if has_data:
            extracted_data.append(row_dict)

    return extracted_data


# ------------------------------------------------------------------
# Shared helpers for the deep parsers
# ------------------------------------------------------------------

def _find_header_row(df: pd.DataFrame, mapping: dict, scan_rows: int = 50) -> int:
    """Scan up to scan_rows rows and return the index of the best header match."""
    header_idx, max_matches = -1, 0
    for idx in range(min(scan_rows, len(df))):
        row_vals      = [str(v).strip().upper() for v in df.iloc[idx].values if pd.notna(v)]
        row_no_spaces = [v.replace(' ', '') for v in row_vals]
        matches = sum(
            1 for expected in mapping
            if (expected.upper() in row_vals
                or expected.upper().replace(' ', '') in row_no_spaces)
        )
        if matches > max_matches:
            max_matches = matches
            header_idx  = idx
    return header_idx if max_matches > 0 else -1


def _build_col_map(columns, mapping: dict) -> dict:
    """Return {actual_col_name: target_db_key} by fuzzy-matching against mapping keys."""
    col_map = {}
    for source_key, target_key in mapping.items():
        expected = source_key.upper()
        for col in columns:
            if expected == col or expected.replace(' ', '') == col.replace(' ', ''):
                col_map[col] = target_key
                break
    return col_map


def _get_col(row, col_map: dict, target_key: str) -> str:
    """Safely extract the value for a target_key from a row using col_map."""
    for col, key in col_map.items():
        if key == target_key:
            val = str(row[col]).strip()
            return '' if _is_blank(val) else val
    return ''


# ==============================================================
# STAGE 3 — NORMALIZE
# ==============================================================

def normalize_rows(parsed_data: list) -> list:
    """Clean item codes and barcodes; strip the '.0' float artifact from Excel."""
    normalized = []
    for row in parsed_data:
        item_raw = re.sub(r'\.0+$', '', str(row.get('ITEM_CODE', '')).strip())
        bar_raw  = re.sub(r'\.0+$', '', str(row.get('BARCODE',   '')).strip())

        if _is_blank(item_raw) and _is_blank(bar_raw):
            continue

        normalized_row = dict(row)
        normalized_row['ITEM_CODE']     = item_raw.upper()
        normalized_row['BARCODE']       = ''.join(c for c in bar_raw if c.isdigit())
        normalized_row['ITEM_CODE_RAW'] = item_raw
        normalized_row['BARCODE_RAW']   = bar_raw
        normalized.append(normalized_row)

    return normalized


# ==============================================================
# STAGE 4 — VALIDATE ROWS (LOCAL DB AUDIT)
# ==============================================================

def validate_rows(parsed_data: list, template_type: str) -> tuple:
    """Validate normalized rows against the Barcodes database. Returns (results, db_online, db_error)."""
    parsed_data = normalize_rows(parsed_data)
    results: list = []
    db_error: Optional[str] = None

    conn, cursor = _get_barcodes_conn()
    if conn is None:
        db_error = 'Could not connect to Barcodes Database'
        logger.error(db_error)

    seen_barcodes: dict = {}
    seen_items: dict = {}

    for row in parsed_data:
        item_code = row.get('ITEM_CODE', '').strip()
        barcode   = row.get('BARCODE',   '').strip()
        result    = dict(row)

        if not barcode or _is_blank(barcode):
            result.update({'status': 'rejected', 'reason': 'VR-002: Barcode is empty'})
            results.append(result)
            continue

        if not item_code or _is_blank(item_code):
            result.update({'status': 'rejected', 'reason': 'VR-003: Item Code is empty'})
            results.append(result)
            continue

        if barcode in seen_barcodes:
            result.update({'status': 'duplicate', 'reason': 'VR-004: Duplicate Barcode in this file'})
            results.append(result)
            continue
        seen_barcodes[barcode] = True

        if item_code in seen_items:
            result.update({'status': 'duplicate', 'reason': 'VR-005: Duplicate Item Code in this file'})
            results.append(result)
            continue
        seen_items[item_code] = True

        if conn and cursor:
            try:
                cursor.execute(
                    'SELECT ITEM_CODE, BARCODE FROM dbo.barcodes WHERE BARCODE = ? OR ITEM_CODE = ?',
                    (barcode, item_code),
                )
                db_rows = cursor.fetchall()

                if not db_rows:
                    result.update({'status': 'update', 'reason': 'New Item & Barcode - Ready to Add'})
                else:
                    conflict_found = False
                    ok_found = False

                    for db_row in db_rows:
                        db_item = str(db_row[0]).strip().upper()
                        db_bar  = str(db_row[1]).strip().upper()

                        if db_bar == barcode.upper() and db_item == item_code.upper():
                            ok_found = True
                        elif db_bar == barcode.upper():
                            result.update({
                                'status': 'conflict',
                                'reason': f'Conflict: Barcode belongs to Item [{db_item}]',
                            })
                            conflict_found = True
                            break
                        elif db_item == item_code.upper():
                            result.update({
                                'status': 'conflict',
                                'reason': f'Conflict: Item already has Barcode [{db_bar}]',
                            })
                            conflict_found = True
                            break

                    if not conflict_found and ok_found:
                        result.update({'status': 'ok', 'reason': 'Exists in Database - Already Validated'})

            except Exception as exc:
                result.update({'status': 'rejected', 'reason': f'DB Error: {exc}'})
        else:
            result.update({
                'status': 'db_offline',
                'reason': 'Validation Offline — Cannot reach Database',
            })

        results.append(result)

    if conn:
        conn.close()

    return results, conn is not None and db_error is None, db_error


# ==============================================================
# STAGE 5 — COMMIT (WRITE NEW BARCODES TO DB)
# ==============================================================

def commit_rows_to_db(rows: list, committed_by: str) -> dict:
    """Write rows with status='update' or 'force_overwrite' to the Barcodes database."""
    conn, cursor = _get_barcodes_conn()
    if conn is None:
        return {
            'success': False, 'committed': 0, 'skipped': 0, 'errors': [],
            'message': 'Cannot commit — Barcodes DB is unreachable',
        }

    committed = 0
    skipped   = 0
    errors: list = []

    try:
        for row in rows:
            status = row.get('status')
            if status not in ('update', 'force_overwrite'):
                skipped += 1
                continue

            item_code = row.get('ITEM_CODE',   '').strip()
            barcode   = row.get('BARCODE',     '').strip()
            desc      = row.get('DESC',        '').strip()
            vendor    = row.get('VENDOR',      '').strip()
            brand     = row.get('BRAND_CODE',  '').strip()
            sku       = row.get('SKU',         '').strip()

            try:
                if status == 'update':
                    # Standard Insert for brand new items
                    cursor.execute(
                        """
                        IF NOT EXISTS (SELECT 1 FROM dbo.barcodes WHERE BARCODE = ? OR ITEM_CODE = ?)
                        BEGIN
                            INSERT INTO dbo.barcodes
                                (ITEM_CODE, BARCODE, [DESC], VENDOR, BRAND_CODE, SKU, DATEADDED)
                            VALUES (?, ?, ?, ?, ?, ?, GETDATE())
                        END
                        """,
                        (barcode, item_code, item_code, barcode, desc, vendor, brand, sku),
                    )
                    committed += 1
                    
                elif status == 'force_overwrite':
                    # 1. Wipe out any old conflicting mappings for this specific Item or Barcode
                    cursor.execute("DELETE FROM dbo.barcodes WHERE BARCODE = ? OR ITEM_CODE = ?", (barcode, item_code))
                    # 2. Force the new 1:1 mapping into the database
                    cursor.execute(
                        """
                        INSERT INTO dbo.barcodes
                            (ITEM_CODE, BARCODE, [DESC], VENDOR, BRAND_CODE, SKU, DATEADDED)
                        VALUES (?, ?, ?, ?, ?, ?, GETDATE())
                        """,
                        (item_code, barcode, desc, vendor, brand, sku),
                    )
                    committed += 1

            except Exception as exc:
                errors.append({'BARCODE': barcode, 'error': str(exc)})

        conn.commit()

    except Exception as exc:
        errors.append({'BARCODE': 'BATCH', 'error': str(exc)})
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()

    _write_audit_log(rows, committed_by, committed, errors)

    return {
        'success':   not errors,
        'committed': committed,
        'skipped':   skipped,
        'errors':    errors,
        'message':   f'{committed} new barcode(s) saved, {skipped} skipped, {len(errors)} error(s)',
    }


# ==============================================================
# STAGE 6 — AUDIT LOG (WRITES TO DSRT DATABASE)
# ==============================================================

def _write_audit_log(rows: list, committed_by: str, committed_count: int, errors: list) -> None:
    """Append commit results to dbo.audit_logs in the DSRT database."""
    conn, cursor = _get_dsrt_conn()
    if conn is None:
        logger.error('Audit log skipped — cannot connect to DSRT DB')
        return

    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for row in rows:
            status = row.get('status')
            if status not in ('update', 'force_overwrite'):
                continue
                
            item = row.get('ITEM_CODE')
            bar  = row.get('BARCODE')
            had_error = any(e.get('BARCODE') == bar for e in errors)
            
            if had_error:
                action = f'Failed to commit barcode {bar} for item {item} (Validation Error)'
            elif status == 'force_overwrite':
                action = f'OVERWROTE existing conflict with new barcode {bar} for item {item}'
            else:
                action = f'Committed new barcode {bar} for item {item}'
                
            cursor.execute(
                'INSERT INTO dbo.audit_logs ([user], [action], [timestamp]) VALUES (?, ?, ?)',
                (committed_by, action, now),
            )
        conn.commit()
        logger.info('Audit log: %d entries written to DSRT by %s', committed_count, committed_by)
    except Exception as exc:
        logger.error('Audit log write failed: %s', exc)
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


def _save_temp_file(file) -> tuple:
    """Save an uploaded file to a temp path. Returns (filename, temp_filepath)."""
    filename = secure_filename(file.filename)
    suffix = os.path.splitext(filename)[1]
    fd, temp_filepath = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    file.save(temp_filepath)
    return filename, temp_filepath


@transactions_bp.route('/api/get_sheets', methods=['POST'])
def get_sheets():
    """Return the list of sheet names for a multi-sheet Excel file."""
    if not session.get('sdr_loggedin'):
        return jsonify({'error': 'Not authenticated'}), 401
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    temp_filepath = None
    try:
        filename, temp_filepath = _save_temp_file(file)
        is_csv = filename.lower().endswith('.csv')
        if is_csv:
            return jsonify({'success': True, 'sheets': [], 'is_csv': True}), 200

        wb = openpyxl.load_workbook(temp_filepath, read_only=True, data_only=True)
        sheet_names = wb.sheetnames
        wb.close()
        return jsonify({'success': True, 'sheets': sheet_names, 'is_csv': False}), 200
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)


@transactions_bp.route('/api/detect_template', methods=['POST'])
def detect_template():
    """Stage 1: Accept a file and return the auto-detected template type."""
    if not session.get('sdr_loggedin'):
        return jsonify({'error': 'Not authenticated'}), 401
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    temp_filepath = None
    try:
        filename, temp_filepath = _save_temp_file(file)
        sheet_index = int(request.form.get('sheet_index', 0))
        detected = detect_template_type(temp_filepath, sheet_index)
        return jsonify({'success': True, 'template_type': detected, 'filename': filename}), 200
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
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
    sheet_index   = int(request.form.get('sheet_index', 0))

    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400
    if not template_type:
        return jsonify({'error': 'Missing template_type'}), 400

    temp_filepath = None
    try:
        filename, temp_filepath = _save_temp_file(file)
        if not filename:
            return jsonify({'error': 'Invalid filename'}), 400

        parsed_data = parse_sku_template(temp_filepath, template_type, sheet_index)
        if not parsed_data:
            return jsonify({
                'error': f'No data extracted using template [{template_type}]. Check the file format.'
            }), 400

        validated_data, db_online, db_error = validate_rows(parsed_data, template_type)

        statuses = [r['status'] for r in validated_data]
        summary = {s: statuses.count(s) for s in ('ok', 'update', 'conflict', 'rejected', 'duplicate', 'db_offline')}

        logger.info(
            '[parse_sku_file] user=%s template=%s rows=%d db_online=%s summary=%s',
            session.get('sdr_curr_user_username'), template_type,
            len(validated_data), db_online, summary,
        )

        return jsonify({
            'success':   True,
            'row_count': len(validated_data),
            'summary':   summary,
            'data':      validated_data,
            'db_online': db_online,
            'db_error':  db_error,
        }), 200

    except Exception as exc:
        logger.error('parse_sku_file error: %s', traceback.format_exc())
        return jsonify({'error': f'Failed to process file: {exc}'}), 500
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)


@transactions_bp.route('/api/commit_barcodes', methods=['POST'])
def commit_barcodes():
    """Stages 5–6: Write approved rows to DB and write audit log."""
    if not session.get('sdr_loggedin'):
        return jsonify({'error': 'Not authenticated'}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({'error': 'No payload received'}), 400

    rows = payload.get('data', [])
    if not rows:
        return jsonify({'error': 'No data to commit'}), 400

    committed_by = session.get('sdr_curr_user_username', 'unknown')
    
    # Check for both 'update' and 'force_overwrite' statuses
    updateable = [r for r in rows if r.get('status') in ('update', 'force_overwrite')]

    if not updateable:
        return jsonify({
            'success': True, 'committed': 0, 'skipped': len(rows),
            'errors': [], 'message': 'No rows with status=update or force_overwrite — nothing to commit',
        }), 200

    result = commit_rows_to_db(rows, committed_by)

    logger.info(
        '[commit_barcodes] user=%s committed=%d skipped=%d errors=%d',
        committed_by, result['committed'], result['skipped'], len(result['errors']),
    )

    return jsonify(result), 200 if result['success'] else 207


@transactions_bp.route('/api/barcode_audit_log', methods=['GET'])
def get_audit_log():
    """Return the last 200 entries from the DSRT audit_logs table."""
    if not session.get('sdr_loggedin'):
        return jsonify({'error': 'Not authenticated'}), 401

    conn, cursor = _get_dsrt_conn()
    if conn is None:
        return jsonify({'error': 'Cannot connect to DSRT DB'}), 500

    try:
        cursor.execute(
            'SELECT TOP 200 id, [user], [action], [timestamp] '
            'FROM dbo.audit_logs ORDER BY [timestamp] DESC'
        )
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

        for r in rows:
            ts = r.get('timestamp')
            if ts and hasattr(ts, 'strftime'):
                r['timestamp'] = ts.strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({'success': True, 'count': len(rows), 'data': rows}), 200

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
    finally:
        conn.close()


# ==============================================================
# ROUTES — PROGRESS STREAM
# ==============================================================

@transactions_bp.route('/progress')
def progress():
    req_id = request.args.get('id', 'default')

    def generate():
        max_polls = 600  # ~5 minutes at 0.5 s intervals before giving up
        for _ in range(max_polls):
            data = get_progress_data(req_id)
            yield f'data: {json.dumps(data)}\n\n'
            if data['status'] == 'Finalizing...' or (data['total'] > 0 and data['current'] >= data['total']):
                break
            time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream')


# ==============================================================
# ROUTES — MISC
# ==============================================================

@transactions_bp.route('/verify-codes', methods=['POST'])
def verify_codes():
    pc_memo           = request.form.get('pc_memo',  '').strip().upper()
    sales_code        = request.form.get('sales_code', '').strip().upper()
    company_selection = request.form.get('company',  '').strip().upper()

    is_atcrep    = company_selection in ('ATC', 'TPC')
    db_target    = 'ATCREP' if is_atcrep else 'NICREP'
    table_prefix = {
        'ATC': 'About Time Corporation',
        'TPC': 'Transcend Prime Inc',
    }.get(company_selection, 'Newtrends International Corp_')

    conn = None
    try:
        conn, cursor, _ = SQLconnect(db_target, 'DSRT')
        if conn is None:
            return jsonify({'success': False, 'error': f'Connection to {db_target} Failed'}), 500

        check_qry = (
            f'SELECT COUNT(*) as cnt FROM dbo."{table_prefix}$Sales Price" WITH (NOLOCK) '
            f'WHERE "Sales Code"=? AND "PC Memo No"=?'
        )
        cursor.execute(check_qry, (sales_code, pc_memo))
        result = cursor.fetchone()

        if result and result[0] > 0:
            return jsonify({'success': True, 'count': result[0]})
        return jsonify({'success': False, 'error': f'No records found in {db_target}'})

    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)})
    finally:
        if conn:
            conn.close()


@transactions_bp.route('/get-companies/<chain>')
def get_companies(chain):
    mysql_conn = get_mysql_conn()
    results = []
    if mysql_conn:
        try:
            cursor = mysql_conn.cursor(dictionary=True)
            cursor.execute(
                'SELECT company_selection, vendor_code FROM vendor_chain_mappings WHERE chain_name = %s',
                (chain.upper(),),
            )
            results = cursor.fetchall()
        finally:
            mysql_conn.close()

    if not results:
        return jsonify([
            {'company_selection': 'NIC', 'vendor_code': None, 'is_default': True},
            {'company_selection': 'ATC', 'vendor_code': None, 'is_default': True},
            {'company_selection': 'TPC', 'vendor_code': None, 'is_default': True},
        ])

    return jsonify(results)


# ==============================================================
# MAIN CONTROLLER ROUTE
# ==============================================================

# Data-mapping helpers ─────────────────────────────────────────

def _build_sm_watsons_cols(merged_df: pd.DataFrame, time_now: datetime, chain_selection: str) -> tuple:
    """Shared column setup for SM and Watsons chains."""
    safe_color = merged_df['Dial Color'].fillna('')        if 'Dial Color'       in merged_df.columns else ''
    safe_size  = merged_df['Case _Frame Size'].fillna('')  if 'Case _Frame Size' in merged_df.columns else ''
    safe_brand = merged_df['Brand'].fillna('')
    safe_desc  = merged_df['Description'].fillna('')
    safe_style = merged_df['Style_Stockcode'].fillna('')

    merged_df['COLOR'] = safe_color
    merged_df['SIZES'] = safe_size
    merged_df['DESCRIPTION'] = (
        (safe_brand + ' ' + safe_desc + ' ' + safe_color + ' ' + safe_size + ' ' + safe_style)
        .str.replace(r'[^a-zA-Z0-9\s]', '', regex=True)
        .str.replace(r'\s+', ' ', regex=True)
        .str.strip()
        .str[:50]
    )
    merged_df['SRP']           = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
    merged_df['EXP_DEL_MONTH'] = (time_now + timedelta(days=30)).strftime('%m/%d/%Y')
    merged_df['SOURCE_MARKED'] = ''
    merged_df['REMARKS']       = ''
    merged_df['ONLINE ITEMS']  = 'YES' if chain_selection == 'WATSONS ONLINE' else 'NO'
    merged_df['PACKAGE WEIGHT IN KG']  = merged_df['Gross Weight']
    merged_df['PRODUCT WEIGHT IN KG']  = merged_df['Net Weight']
    merged_df['IMAGES'] = ''

    for dim in ['PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM',
                'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM']:
        merged_df[dim] = '-'

    final_cols = [
        'DESCRIPTION', 'COLOR', 'SIZES', 'Style_Stockcode', 'SOURCE_MARKED',
        'SRP', 'Unit_of_Measure', 'EXP_DEL_MONTH', 'REMARKS', 'IMAGES', 'ONLINE ITEMS',
        'PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 'PACKAGE WEIGHT IN KG',
        'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM', 'PRODUCT WEIGHT IN KG',
    ]
    return final_cols, 'IMAGES', 'Template', 0, 1


def _map_rds(merged_df: pd.DataFrame, vendor_code: str, dynamic_mfg_no: str) -> tuple:
    """Build RDS column layout. Returns (final_cols, img_col, sheet, header_row, data_row)."""
    # PAGE 1
    for col in ('SKU Number', 'SKU Number with check digit', 'Sku Number'):
        merged_df[col] = ''
    merged_df['Item Description'] = merged_df['Description'].fillna('').str[:30]
    merged_df['Short name']       = merged_df['Description'].fillna('').str[:10]
    merged_df['Item Status']      = 'A'
    merged_df['Buyer']            = 'B92'
    merged_df['W/SCD 5% DISC']   = 'N'
    merged_df['Inventory Grp']    = ''
    merged_df['W/PWD 5% DISC']   = 'N'
    merged_df['SKU Type']         = ''
    merged_df['Merchandiser']     = ''
    merged_df['POS Tax Code']     = 'V'
    merged_df['Primary Vendor']   = vendor_code
    merged_df['Ship Pt']          = ''
    merged_df['Manufacturer']     = ''
    merged_df['Vendor Part#']     = ''
    merged_df['Manufacturer Part#'] = dynamic_mfg_no
    for col in ('Dept', 'Sub-Dept', 'Class-', 'Sub-Class'):
        merged_df[col] = ''
    # PAGE 2
    for col in ('Product Code', 'TYPE', 'Primary Buy UPC', 'Saleable UPC'):
        merged_df[col] = ''
    # PAGE 3
    for col in ('Competitive Priced', 'Display on Web', 'Competitive Price', 'POS Price Prompt'):
        merged_df[col] = ''
    merged_df['Original Price']       = merged_df['SRP'].fillna(0).map('{:.2f}'.format)
    merged_df['Prevent POS Download'] = 'N'
    for col in ('Next Regular Retail', 'Effective', 'Current Vendor Cost'):
        merged_df[col] = ''
    merged_df['Buying U/M']        = 'PCS'
    merged_df['Selling U/M']       = 'PCS'
    merged_df['Standard Pack']     = '-'
    merged_df['Minimum (Inner) Pack'] = '-'
    # PAGE 4
    merged_df['Coordinate Group']    = 'RDS'
    merged_df['Super Brand']         = ''
    merged_df['Brand_Maint']         = merged_df['Brand'].fillna('')
    merged_df['Buy Code(C/S)']       = 'S'
    merged_df['Season']              = 'NA'
    for col in ('Set Code', 'Mfg. No.', 'Age Code', 'Label', 'Origin', 'Tag', 'Fair Event', 'Blank Field'):
        merged_df[col] = '-'
    merged_df['Price Point']         = merged_df['Point_Power'].fillna('')
    merged_df['Merchandise Flag']    = '-'
    merged_df['Hold Wholesale Order'] = 'N'
    merged_df['Size']                = merged_df['Case _Frame Size'].fillna('')
    for col in ('Substitute SKU', 'Core SKU', 'Replacement SKU'):
        merged_df[col] = ''
    # PAGE 5
    merged_df['Replenishment Code'] = '0'
    for col in ('Sales $ (Blank)', 'Distribution Method', 'Sales Units', 'Rpl Start Date',
                'Gross Margin', 'Rpl End Date', 'User Defined', 'Avg. Model Stock',
                'Avg. Order at', 'Maximum Stock', 'Display Minimum', 'Stock in Mult. of'):
        merged_df[col] = ''
    merged_df['Minimum Rpl Qty'] = '-'
    merged_df['Item Profile']    = ''
    merged_df['Hold Order']      = 'N'
    merged_df['Plan Lead Time']  = ''
    # PAGE 6
    merged_df['Item Weight'] = merged_df['Gross Weight']
    for col in ('Item Length', 'Width', 'Height', 'Item Cube', 'Pallet Tie', 'Pallet High',
                'Container Type', 'Container Multiple'):
        merged_df[col] = ''
    # PAGE 7
    for col in ('Regular Label Type', 'Ad Label Type', 'Regular Ticket  Type', 'Ad Ticket Type',
                'Tickets per Item'):
        merged_df[col] = ''
    merged_df['Is Sign Age Required'] = 'N'
    # PAGE 8
    merged_df['Commercial Inv Product'] = ''
    merged_df['Selling Unit Weight']    = merged_df['Net Weight']
    for col in ('Descriptor', 'Derived Description', '12 Character', '15 Character',
                '18 Character', '21 Character', '20 Character', 'Shelf Label', 'Blank Field'):
        merged_df[col] = ''
    merged_df['Color']  = merged_df['Dial Color'].fillna('')
    merged_df['Size_P8'] = merged_df['Case _Frame Size'].fillna('')
    merged_df['Dimension'] = ''

    p1 = ['SKU Number', 'SKU Number with check digit', 'Sku Number', 'Item Description',
          'Short name', 'Item Status', 'Buyer', 'W/SCD 5% DISC', 'Inventory Grp',
          'W/PWD 5% DISC', 'SKU Type', 'Merchandiser', 'POS Tax Code', 'Primary Vendor',
          'Ship Pt', 'Manufacturer', 'Vendor Part#', 'Manufacturer Part#',
          'Dept', 'Sub-Dept', 'Class-', 'Sub-Class']
    p2 = ['Product Code', 'TYPE', 'Primary Buy UPC', 'Saleable UPC']
    p3 = ['Competitive Priced', 'Display on Web', 'Competitive Price', 'POS Price Prompt',
          'Original Price', 'Prevent POS Download', 'Next Regular Retail', 'Effective',
          'Current Vendor Cost', 'Buying U/M', 'Selling U/M', 'Standard Pack', 'Minimum (Inner) Pack']
    p4 = ['Coordinate Group', 'Super Brand', 'Brand_Maint', 'Buy Code(C/S)', 'Season',
          'Set Code', 'Mfg. No.', 'Age Code', 'Label', 'Origin', 'Tag', 'Fair Event',
          'Blank Field', 'Price Point', 'Merchandise Flag', 'Hold Wholesale Order', 'Size',
          'Substitute SKU', 'Core SKU', 'Replacement SKU']
    p5 = ['Replenishment Code', 'Sales $ (Blank)', 'Distribution Method', 'Sales Units',
          'Rpl Start Date', 'Gross Margin', 'Rpl End Date', 'User Defined', 'Avg. Model Stock',
          'Avg. Order at', 'Maximum Stock', 'Display Minimum', 'Stock in Mult. of',
          'Minimum Rpl Qty', 'Item Profile', 'Hold Order', 'Plan Lead Time']
    p6 = ['Item Weight', 'Item Length', 'Width', 'Height', 'Item Cube',
          'Pallet Tie', 'Pallet High', 'Container Type', 'Container Multiple']
    p7 = ['Regular Label Type', 'Ad Label Type', 'Regular Ticket  Type',
          'Ad Ticket Type', 'Tickets per Item', 'Is Sign Age Required']
    p8 = ['Commercial Inv Product', 'Selling Unit Weight', 'Descriptor', 'Derived Description',
          '12 Character', '15 Character', '18 Character', '21 Character', '20 Character',
          'Shelf Label', 'Blank Field', 'Color', 'Size_P8', 'Dimension']

    rds_sections = [
        (p1, 'PAGE 1 - Item Base Data Maintenance',        '#BDD7EE'),
        (p2, 'PAGE 2 - UPC Maintenance',                   '#E2EFDA'),
        (p3, 'PAGE 3 - Item Cost and Price Maintenance',    '#FFF2CC'),
        (p4, 'PAGE 4 - Item Code Maintenance',              '#EAD1DC'),
        (p5, 'PAGE 5 - Item Replenishment Maintenance',     '#FCE4D6'),
        (p6, 'PAGE 6 - Physical Dimension Maintenance',     '#D9E1F2'),
        (p7, 'PAGE 7 - Label, Tag, and Ticket Maintenance', '#F2F2F2'),
        (p8, 'PAGE 8 - Item Descriptions Maintenance',      '#E7E6E6'),
    ]
    final_layout = []
    for idx, (group, _, _) in enumerate(rds_sections):
        final_layout.extend(group)
        if idx < len(rds_sections) - 1:
            gap_col = f'GAP_{idx}'
            merged_df[gap_col] = ''
            final_layout.append(gap_col)

    return final_layout, rds_sections, None, 'TEMPLATE', 1, 2


@transactions_bp.route('/process-template', methods=['POST'])
def process_template():
    chain_selection   = request.form.get('chain',      '').strip().upper()
    company_selection = request.form.get('company',    '').strip().upper()
    pc_memo           = request.form.get('pc_memo',    '').strip().upper()
    sales_code        = request.form.get('sales_code', '').strip().upper()

    req_id = sales_code
    save_progress(req_id, 0, 0, 'Initializing...')

    # Redirect ATC/TPC to their own processing module
    if company_selection in ('ATC', 'TPC'):
        logger.info('Redirecting to ATC/TPC logic for company: %s', company_selection)
        return process_atcrep_template(
            chain_selection, company_selection, pc_memo, sales_code,
            SQLconnect, get_mysql_conn, build_image_cache,
            find_image_in_cache, NETWORK_IMAGE_PATH, {},
        )

    # ── NIC script logic ──────────────────────────────────────
    save_progress(req_id, 0, 0, 'Accessing NICREP...')
    conn = None
    try:
        conn, cursor, _ = SQLconnect('NICREP', 'DSRT')
        if conn is None:
            return jsonify({'error': 'Database Connection Failed'}), 500

        # Fetch prices (try with Discount Level first, fall back without)
        base_price_qry = (
            'SELECT {cols} FROM ('
            '  SELECT "Item No_", "Unit Price" AS SRP, {extra}'
            '  ROW_NUMBER() OVER (PARTITION BY "Item No_" ORDER BY "Starting Date" DESC) AS RowNum '
            '  FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
            '  WHERE "Sales Code"=? AND "PC Memo No"=?'
            ') t WHERE RowNum = 1'
        )
        try:
            price_qry = base_price_qry.format(
                cols='"Item No_", "SRP", "Price_Discount"',
                extra='"Discount Level" AS "Price_Discount", ',
            )
            prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])
        except Exception:
            price_qry = base_price_qry.format(
                cols='"Item No_", "SRP"',
                extra='',
            )
            prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])

        if prices_df.empty:
            return jsonify({'error': 'No records found in Navision for the provided codes.'}), 404

        item_list         = prices_df['Item No_'].tolist()
        total_items_count = len(item_list)
        save_progress(req_id, 0, total_items_count, f'Found {total_items_count} items. Starting Retrieval...')

        # ── Chunked data retrieval ────────────────────────────
        CHUNK_SIZE = 2000
        items_dfs: list = []
        attr_dfs:  list = []

        for i in range(0, len(item_list), CHUNK_SIZE):
            chunk        = item_list[i:i + CHUNK_SIZE]
            placeholders = ', '.join(['?'] * len(chunk))
            save_progress(req_id, i, total_items_count,
                          f'Retrieving item details... ({i}/{total_items_count})')

            try:
                item_qry = (
                    f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                    f'"Vendor Item No_" AS "Style_Stockcode", "Net Weight", "Gross Weight", '
                    f'"Base Unit of Measure" AS "Unit_of_Measure", "Dial Color", '
                    f'"Case _Frame Size", "Gender", "Case_Frame Material" AS "Material", '
                    f'"Item Category Code", "Discount Level" AS "Item_Discount" '
                    f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                    f'WHERE "No_" IN ({placeholders})'
                )
                chunk_df = pd.read_sql(item_qry, conn, params=chunk)
            except Exception:
                item_qry = (
                    f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                    f'"Vendor Item No_" AS "Style_Stockcode", "Net Weight", "Gross Weight", '
                    f'"Base Unit of Measure" AS "Unit_of_Measure", "Item Category Code" '
                    f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                    f'WHERE "No_" IN ({placeholders})'
                )
                chunk_df = pd.read_sql(item_qry, conn, params=chunk)

            items_dfs.append(chunk_df)

            attr_qry = (
                f'SELECT a."No_", b."Name" AS "Attribute", c."Value" '
                f'FROM dbo."Newtrends International Corp_$Item Attribute Value Mapping" a WITH (NOLOCK) '
                f'LEFT JOIN dbo."Newtrends International Corp_$Item Attribute" b ON a."Item Attribute ID" = b."ID" '
                f'LEFT JOIN dbo."Newtrends International Corp_$Item Attribute Value" c '
                f'  ON a."Item Attribute ID" = c."Attribute ID" AND a."Item Attribute Value ID" = c."ID" '
                f'WHERE a."Table ID" = 27 AND a."No_" IN ({placeholders})'
            )
            try:
                attr_dfs.append(pd.read_sql(attr_qry, conn, params=chunk))
            except Exception as exc:
                logger.error('Attribute fetch failed for chunk %d: %s', i, exc)

            time.sleep(0.01)  # yield to other threads — do not remove

        # ── Reconstruct + merge ───────────────────────────────
        items_df = pd.concat(items_dfs, ignore_index=True) if items_dfs else pd.DataFrame()

        if attr_dfs:
            attr_df = pd.concat(attr_dfs, ignore_index=True)
            if not attr_df.empty:
                pivoted = attr_df.pivot(index='No_', columns='Attribute', values='Value').reset_index()
                pivoted = pivoted.rename(columns={
                    'Pricepoint':       'Point_Power',
                    'Dial Color':       'Dial Color',
                    'Case _Frame Size': 'Case _Frame Size',
                    'Gender':           'Gender',
                })
                items_df = pd.merge(items_df, pivoted, how='left', left_on='Item No_', right_on='No_')

        for col in ('Point_Power', 'Dial Color', 'Case _Frame Size', 'Gender',
                    'Net Weight', 'Gross Weight', 'Item_Discount'):
            if col not in items_df.columns:
                items_df[col] = ''

        merged_df = pd.merge(items_df, prices_df, on='Item No_')

        # Resolve Discount Level from whichever column is available
        if 'Price_Discount' in merged_df and not merged_df['Price_Discount'].isna().all():
            merged_df['Discount Level'] = merged_df['Price_Discount']
        elif 'Item_Discount' in merged_df and not merged_df['Item_Discount'].isna().all():
            merged_df['Discount Level'] = merged_df['Item_Discount']
        else:
            merged_df['Discount Level'] = ''

        # ── Vendor / brand lookup from MySQL ──────────────────
        mysql_conn = get_mysql_conn()
        vendor_code, dynamic_mfg_no = '000000', ''
        if mysql_conn:
            try:
                v_cursor = mysql_conn.cursor()
                v_cursor.execute(
                    'SELECT vendor_code FROM vendor_chain_mappings '
                    'WHERE chain_name = %s AND company_selection = %s',
                    (chain_selection, company_selection),
                )
                v_res = v_cursor.fetchone()
                if v_res:
                    vendor_code = str(v_res[0])
                    v_cursor.execute(
                        'SELECT mfg_part_no FROM vendors_rds WHERE vendor_code = %s',
                        (vendor_code,),
                    )
                    mfg_res = v_cursor.fetchone()
                    if mfg_res:
                        dynamic_mfg_no = str(mfg_res[0])
            finally:
                mysql_conn.close()

        # ── Data mapping per chain ─────────────────────────────
        time_now = datetime.now()
        zip_date = time_now.strftime('%m%d%Y')

        chain_filename_map = {
            'RDS':     (f'RDS {company_selection} {time_now.strftime("%m%d%Y")}',     f'RDS{zip_date}.zip'),
            'RUSTANS': (f'RUSTANS {time_now.strftime("%m%d%Y")} {company_selection}', f'RUSTANS{zip_date}.zip'),
            'GCAP':    (f'GCAP {company_selection} {time_now.strftime("%m%d%Y")}',    f'GCAP{zip_date}.zip'),
            'KCC':     (f'KCC SKU {time_now.strftime("%m%d%Y")} {company_selection}', f'KCC{zip_date}.zip'),
        }

        if chain_selection in chain_filename_map:
            filename_base, final_zip_name = chain_filename_map[chain_selection]
        elif chain_selection in ('GGRAND', 'ALTURAS', 'METRO'):
            filename_base = f'{chain_selection} {company_selection} {time_now.strftime("%m%d%Y")}'
            final_zip_name = f'{chain_selection}{zip_date}.zip'
        elif chain_selection in ('WATSONS', 'WATSONS ONLINE'):
            sm_ts = time_now.strftime('%m%d%H%M')
            filename_base  = f'SC{vendor_code}_DEPT_CLASS_{sm_ts}'
            chain_prefix   = 'WATSONS_ONLINE' if chain_selection == 'WATSONS ONLINE' else 'WATSONS'
            final_zip_name = f'{chain_prefix}{zip_date}.zip'
        else:  # SM / default
            sm_ts = time_now.strftime('%m%d%H%M')
            filename_base  = f'SC{vendor_code}_DEPT_CLASS_{sm_ts}'
            final_zip_name = f'SM{zip_date}.zip'

        # img_col_name defaults; overridden per chain below
        img_col_name = None
        rds_sections = None

        if chain_selection == 'RDS':
            final_cols, rds_sections, img_col_name, sheet_name_val, header_row_idx, data_start_row = \
                _map_rds(merged_df, vendor_code, dynamic_mfg_no)

        elif chain_selection == 'RUSTANS':
            merged_df['VENDOR ITEM CODE'] = merged_df['Item No_']
            merged_df['VENDOR CODE']      = vendor_code
            desc_str = (
                merged_df['Description'].fillna('') + ' '
                + merged_df['Dial Color'].fillna('') + ' '
                + merged_df['Style_Stockcode'].fillna('') + ' '
                + merged_df['Brand'].fillna('')
            ).str.strip()
            merged_df['PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)'] = desc_str.str[:30]
            merged_df['PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)']  = merged_df['Description'].fillna('').str[:10]
            merged_df['PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)']   = desc_str.str[:50]
            merged_df['RETAIL PRICE'] = merged_df['SRP'].fillna(0).apply(lambda x: f'{x:.2f}')
            merged_df['SET / PC']     = 'PC'
            merged_df['MATERIAL']     = merged_df['Material'].fillna('')    if 'Material'        in merged_df.columns else ''
            merged_df['Gender']       = merged_df['Gender'].fillna('')      if 'Gender'          in merged_df.columns else ''
            merged_df['Dial Color']   = merged_df['Dial Color'].fillna('')  if 'Dial Color'      in merged_df.columns else ''
            merged_df['Case _Frame Size'] = merged_df['Case _Frame Size'].fillna('') if 'Case _Frame Size' in merged_df.columns else ''

            for col in ('RCC SKU', 'IMAGE', 'BRAND CODE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS',
                        'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION',
                        'SIZE RUN', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD',
                        'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS',
                        'PRODUCT & CARE DETAILS', 'LINK TO HI-RES IMAGE'):
                merged_df[col] = ''

            final_cols = [
                'RCC SKU', 'IMAGE', 'VENDOR ITEM CODE',
                'PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)',
                'PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)',
                'PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)',
                'VENDOR CODE', 'BRAND CODE', 'RETAIL PRICE', 'DEPARTMENT', 'SUBDEPARTMENT',
                'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME',
                'COLLECTION', 'Dial Color', 'SIZE RUN', 'Case _Frame Size', 'SET / PC',
                'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)',
                'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS',
                'MATERIAL', 'LINK TO HI-RES IMAGE', 'Gender',
            ]
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGE', 'Rustans Template', 14, 15

        elif chain_selection == 'GCAP':
            merged_df['brand']          = merged_df['Brand'].fillna('')
            merged_df['item code']      = merged_df['Item No_']
            merged_df['promo category'] = merged_df['Description'].fillna('').apply(
                lambda x: 'PROMO ITEM' if ('@' in str(x) or '#' in str(x)) else 'REGULAR ITEM'
            )
            merged_df['item category'] = merged_df['Item Category Code'].apply(_abbreviate_category)
            merged_df['price']         = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            merged_df['description']   = (
                merged_df['Description'].fillna('').astype(str) + ' '
                + merged_df['item code'].astype(str) + ' '
                + merged_df['price'].astype(str)
            ).str.strip()

            final_cols = ['brand', 'item code', 'promo category', 'item category', 'description', 'price']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = None, 'GCAP Template', 0, 1

        elif chain_selection == 'KCC':
            merged_df['SKU']                = merged_df['Item No_']
            merged_df['BARCODE']            = ''
            merged_df['ITEM CODE/STOCK#']   = merged_df['Style_Stockcode'].fillna('')
            merged_df['BRAND']              = merged_df['Brand'].fillna('')
            merged_df['DESCRIPTION']        = merged_df['Description'].fillna('')
            merged_df['REGULAR PRICE']      = pd.to_numeric(merged_df['Point_Power'], errors='coerce').fillna(0).map('{:,.2f}'.format)
            merged_df['MARKDOWN PRICE']     = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            merged_df['SPECIFICATION']      = (merged_df['Dial Color'].fillna('') + ' ' + merged_df['Case _Frame Size'].fillna('')).str.strip()
            merged_df['SAMPLE IMAGE']       = ''
            merged_df['PRICE CATEGORY']     = 'SALE ITEM'
            merged_df['DISCOUNT LEVEL']     = merged_df['Discount Level'].fillna('')

            final_cols = [
                'SKU', 'BARCODE', 'ITEM CODE/STOCK#', 'BRAND', 'DESCRIPTION',
                'REGULAR PRICE', 'MARKDOWN PRICE', 'SPECIFICATION', 'SAMPLE IMAGE',
                'PRICE CATEGORY', 'DISCOUNT LEVEL',
            ]
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'SAMPLE IMAGE', 'Sheet1', 5, 6

        elif chain_selection in ('GGRAND', 'ALTURAS'):
            merged_df['BRAND']          = merged_df['Brand'].fillna('')
            merged_df['PROMO CATEGORY'] = merged_df['Description'].fillna('').apply(
                lambda x: 'PROMO ITEM' if ('@' in str(x) or '#' in str(x)) else 'SALE ITEM'
            )
            merged_df['ITEM CATEGORY']  = merged_df['Item Category Code'].apply(_abbreviate_category)
            merged_df['PRICE']          = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            merged_df['DESCRIPTION']    = (
                merged_df['Description'].fillna('').astype(str) + ' '
                + merged_df['PRICE'].astype(str) + ' '
                + merged_df['ITEM CATEGORY'].astype(str)
            ).str.strip()
            merged_df['SKU']     = ''
            merged_df['BARCODE'] = ''

            final_cols = ['BRAND', 'PROMO CATEGORY', 'ITEM CATEGORY', 'DESCRIPTION', 'PRICE', 'SKU', 'BARCODE']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = \
                None, f'{chain_selection.title()} Template', 2, 3

        elif chain_selection in ('WATSONS', 'WATSONS ONLINE'):
            final_cols, img_col_name, sheet_name_val, header_row_idx, data_start_row = \
                _build_sm_watsons_cols(merged_df, time_now, chain_selection)

        elif chain_selection == 'METRO':
            merged_df['NO']             = range(1, len(merged_df) + 1)
            merged_df['PRODUCT IMAGE']  = ''
            merged_df['DEPT']           = '5926'
            merged_df['CLASS']          = '1'
            merged_df['SUBCLASS']       = '1'
            merged_df['EAN-13']         = ''
            merged_df['BRAND NAME']     = merged_df['Brand'].fillna('')
            merged_df['ITEM DESCRIPTION']   = merged_df['Description'].fillna('')
            merged_df['STOCK/ PRODUCT CODE'] = merged_df['Style_Stockcode'].fillna('')
            merged_df['COLOR']          = merged_df['Dial Color'].fillna('')  if 'Dial Color'       in merged_df.columns else ''
            merged_df['SIZE']           = merged_df['Case _Frame Size'].fillna('') if 'Case _Frame Size' in merged_df.columns else ''
            merged_df['MATERIAL/FABRIC'] = merged_df['Material'].fillna('')  if 'Material'         in merged_df.columns else ''
            merged_df['REGULAR PRICE']  = pd.to_numeric(merged_df['SRP'], errors='coerce').fillna(0)
            merged_df['SALE PRICE']     = ''
            merged_df['STOCK AVAILABILITY'] = 'FEBRUARY ONWARDS'
            for i in range(27):
                merged_df[f'Store_{i}'] = ''
            for col in ('TOTAL QTY', 'APPROVED', 'DISAPPROVED', 'SKU', 'UPC', 'MDSG REMARKS'):
                merged_df[col] = ''

            final_cols = (
                ['NO', 'PRODUCT IMAGE', 'DEPT', 'CLASS', 'SUBCLASS', 'EAN-13', 'BRAND NAME',
                 'ITEM DESCRIPTION', 'STOCK/ PRODUCT CODE', 'COLOR', 'SIZE', 'MATERIAL/FABRIC',
                 'REGULAR PRICE', 'SALE PRICE', 'STOCK AVAILABILITY']
                + [f'Store_{i}' for i in range(27)]
                + ['TOTAL QTY', 'APPROVED', 'DISAPPROVED', 'SKU', 'UPC', 'MDSG REMARKS']
            )
            img_col_name, sheet_name_val, header_row_idx, data_start_row = \
                'PRODUCT IMAGE', 'New Item Sample Sheet', 6, 8

        else:  # SM / default
            final_cols, img_col_name, sheet_name_val, header_row_idx, data_start_row = \
                _build_sm_watsons_cols(merged_df, time_now, chain_selection)

        # ── Excel generation ──────────────────────────────────
        output_buffer    = io.BytesIO()
        brand_groups     = list(merged_df.groupby('Brand'))
        images_found_count = 0
        is_multisheet_mode = (chain_selection == 'RUSTANS')
        used_filenames: set = set()
        zip_file       = None
        global_writer  = None

        save_progress(req_id, 0, len(merged_df), 'Initializing Excel Generation...')

        if is_multisheet_mode:
            global_writer = pd.ExcelWriter(output_buffer, engine='xlsxwriter')
        else:
            zip_file = zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED)

        try:
            for brand_name, bucket_df in brand_groups:
                try:
                    # 1. Determine filename (zip mode only)
                    filename = ''
                    if not is_multisheet_mode:
                        if chain_selection in ('RDS', 'GCAP', 'KCC', 'GGRAND', 'ALTURAS', 'METRO'):
                            filename = f'{filename_base} - {brand_name}.xlsx'
                        else:
                            f_dept, f_class = '0000', '0000'
                            loop_conn = get_mysql_conn()
                            if loop_conn:
                                try:
                                    l_cursor = loop_conn.cursor(dictionary=True)
                                    l_cursor.execute(
                                        'SELECT b.dept_code, b.sub_dept_code, b.class_code, s.subclass_code '
                                        'FROM brands b LEFT JOIN sub_classes s ON b.product_group = s.product_group '
                                        'WHERE b.brand_name LIKE %s LIMIT 1',
                                        (str(brand_name).strip() + '%',),
                                    )
                                    res = l_cursor.fetchone()
                                    if res:
                                        f_dept  = f"{res.get('dept_code') or '00'}{res.get('sub_dept_code') or '00'}"
                                        f_class = f"{res.get('class_code') or '00'}{res.get('subclass_code') or '00'}"
                                except Exception as db_e:
                                    logger.error('Loop Lookup Error: %s', db_e)
                                finally:
                                    loop_conn.close()
                            sm_ts    = time_now.strftime('%m%d%H%M')
                            filename = f'SC{vendor_code}_{f_dept}_{f_class}_{sm_ts}.xlsx'

                        # Deduplicate filenames
                        if filename in used_filenames:
                            base, ext = os.path.splitext(filename)
                            counter = 1
                            while f'{base}_{counter}{ext}' in used_filenames:
                                counter += 1
                            filename = f'{base}_{counter}{ext}'
                        used_filenames.add(filename)

                    save_progress(req_id, 0, len(merged_df), f'Processing Brand: {brand_name}')

                    # 2. Set up writer and sheet
                    if is_multisheet_mode:
                        current_writer     = global_writer
                        current_sheet_name = _safe_sheet_name(brand_name)
                        data_start_row     = 12
                    else:
                        excel_output       = io.BytesIO()
                        current_writer     = pd.ExcelWriter(excel_output, engine='xlsxwriter')
                        current_sheet_name = sheet_name_val
                        data_start_row     = {'METRO': 8, 'RDS': 2, 'KCC': 6,
                                              'GGRAND': 3, 'ALTURAS': 3}.get(chain_selection, 1)

                    # 3. Write data
                    bucket_df[final_cols].to_excel(
                        current_writer, sheet_name=current_sheet_name,
                        index=False, startrow=data_start_row, header=False,
                    )
                    workbook  = current_writer.book
                    worksheet = current_writer.sheets[current_sheet_name]

                    # 4. Formatting per chain
                    if chain_selection == 'RDS':
                        curr_col = 0
                        for idx, (group, title, color) in enumerate(rds_sections):
                            page_hdr_fmt  = workbook.add_format({'bold': True, 'bg_color': color, 'border': 1, 'align': 'center', 'font_size': 11})
                            field_hdr_fmt = workbook.add_format({'bold': True, 'bg_color': color, 'border': 1, 'align': 'center', 'font_size': 9})
                            worksheet.merge_range(0, curr_col, 0, curr_col + len(group) - 1, title, page_hdr_fmt)
                            for field in group:
                                display_name = {'Size_P8': 'Size', 'Brand_Maint': 'Brand'}.get(field, field)
                                worksheet.write(1, curr_col, display_name, field_hdr_fmt)
                                worksheet.set_column(curr_col, curr_col, 18 if 'Description' in field else 13)
                                curr_col += 1
                            if idx < len(rds_sections) - 1:
                                worksheet.set_column(curr_col, curr_col, 2)
                                curr_col += 1

                    elif chain_selection == 'RUSTANS':
                        bold_fmt  = workbook.add_format({'bold': True})
                        title_fmt = workbook.add_format({'bold': True, 'font_size': 11})
                        worksheet.write(0, 0, 'RUSTAN COMMERCIAL CORPORATION', title_fmt)
                        worksheet.write(1, 0, 'CONCESSIONAIRE MANAGEMENT DIVISION', bold_fmt)
                        worksheet.write(2, 0, 'NEW PRODUCT INFORMATION SHEET (NPIS)', bold_fmt)
                        worksheet.write(4, 0, 'DATE:', bold_fmt)
                        worksheet.write(4, 1, datetime.now().strftime('%Y-%m-%d'))
                        worksheet.write(4, 5, 'TARGET DELIVERY TO STORES:', bold_fmt)
                        worksheet.write(5, 0, 'DIVISION:', bold_fmt)
                        worksheet.write(5, 5, 'DELIVERY TO E-COMMERCE WAREHOUSE:', bold_fmt)
                        worksheet.write(6, 0, 'COMPANY NAME:', bold_fmt)
                        worksheet.write(6, 1, 'NEWTRENDS INTERNATIONAL CORPORATION')
                        worksheet.write(7, 0, 'BRAND:', bold_fmt)
                        worksheet.write(7, 1, brand_name)

                        instr_fmt = workbook.add_format({'bold': True, 'bg_color': '#FFFF00', 'border': 1, 'align': 'center'})
                        worksheet.merge_range(10, 0, 10, len(final_cols) - 1,
                                              'ALL HIGHLIGHTED COLUMNS IN CHART ARE TO BE FILLED UP BY CONCESSIONAIRE',
                                              instr_fmt)

                        rustans_hdr_fmt = workbook.add_format({
                            'bold': True, 'bg_color': '#F2F2F2', 'border': 1,
                            'align': 'center', 'text_wrap': True, 'font_size': 9,
                        })
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(11, col_num, value, rustans_hdr_fmt)
                            if value != img_col_name:
                                if 'Description' in value:     worksheet.set_column(col_num, col_num, 40)
                                elif 'RCC SKU' in value:       worksheet.set_column(col_num, col_num, 15)
                                elif any(x in value for x in ('Size', 'Color', 'Price')):
                                    worksheet.set_column(col_num, col_num, 12)
                                else:                          worksheet.set_column(col_num, col_num, 18)

                    elif chain_selection == 'GCAP':
                        header_fmt = workbook.add_format({
                            'bold': True, 'bg_color': '#2E75B6', 'font_color': 'white',
                            'border': 1, 'align': 'center',
                        })
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(0, col_num, value, header_fmt)
                            worksheet.set_column(col_num, col_num, 45 if value == 'description' else 15)

                    elif chain_selection == 'KCC':
                        title_fmt  = workbook.add_format({'bold': True, 'font_size': 11})
                        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1, 'align': 'center'})
                        worksheet.write(0, 0, 'KCC MALLS SKU REQUEST FORMAT', title_fmt)
                        worksheet.write(1, 0, "Supplier's Name: ")
                        worksheet.write(2, 0, f'DATE: {datetime.now().strftime("%m/%d/%Y")}')
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(5, col_num, value, header_fmt)
                            if value == 'description':     worksheet.set_column(col_num, col_num, 45)
                            elif value == img_col_name:    worksheet.set_column(col_num, col_num, 35)
                            else:                          worksheet.set_column(col_num, col_num, 18)

                    elif chain_selection in ('GGRAND', 'ALTURAS'):
                        title_fmt  = workbook.add_format({'bold': True, 'font_size': 12})
                        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2', 'border': 1, 'align': 'center'})
                        worksheet.write(0, 0, 'SKU REQUEST TEMPLATE', title_fmt)
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(2, col_num, value, header_fmt)
                            if value == 'DESCRIPTION':                                    worksheet.set_column(col_num, col_num, 40)
                            elif value in ('BRAND', 'PROMO CATEGORY', 'ITEM CATEGORY'):  worksheet.set_column(col_num, col_num, 20)
                            else:                                                         worksheet.set_column(col_num, col_num, 15)

                    elif chain_selection == 'METRO':
                        bold_fmt       = workbook.add_format({'bold': True})
                        hdr_fmt        = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True, 'font_size': 9})
                        red_hdr_fmt    = workbook.add_format({'bold': True, 'bg_color': '#E6B8B7', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True, 'font_size': 9})
                        rotate_hdr_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'font_size': 9, 'rotation': 90})

                        worksheet.write(0, 0,  'VENDOR NAME :',                              bold_fmt)
                        worksheet.write(0, 2,  f'[{company_selection}]',                     bold_fmt)
                        worksheet.write(0, 10, 'ITEM CLASSIFICATION (Please check):',        bold_fmt)
                        worksheet.write(1, 0,  'DEPT/CATEGORY :',                            bold_fmt)
                        worksheet.write(1, 2,  '[Better Accesories]',                        bold_fmt)
                        worksheet.write(1, 10, '[  x  ] REGULAR ITEM',                       bold_fmt)
                        worksheet.write(2, 0,  'BUYING MONTH/YEAR :',                        bold_fmt)
                        worksheet.write(2, 2,  f'[{time_now.strftime("%m/%Y")}]',            bold_fmt)
                        worksheet.write(2, 10, '[     ] PROMOTIONAL/SEASONAL ITEM  | DURATION: __________________', bold_fmt)
                        worksheet.write(4, 0,  'To be filled up by Supplier:',               bold_fmt)
                        worksheet.write(4, 15, 'Initial Quantity Allocation per Store (for Concession Items Only):', bold_fmt)
                        worksheet.write(4, 42, 'To be filled up by Metro Gaisano:',          bold_fmt)
                        worksheet.write(5, 0,  'NEW ITEM DETAILS',                           bold_fmt)
                        worksheet.write(5, 9,  'PRODUCT ATTRIBUTES',                         bold_fmt)
                        worksheet.write(5, 12, 'PRICING',                                    bold_fmt)
                        worksheet.write(5, 14, 'REMARKS',                                    bold_fmt)

                        dept_names = ['Colon', 'Mandaue', 'Ayala', 'Legazpi', 'Lucena', 'Market Market',
                                      'Angeles', 'Alabang', 'Danao', 'Bacolod', 'Tacloban', 'Pasig',
                                      'Baybay', 'Catbalogan', 'Imus']
                        hyp_names  = ['Toledo', 'Maasin', 'Talisay', 'Lapulapu', 'Colon', 'Mambaling',
                                      'Calbayog', 'Carcar', 'Bogo', 'Naga-Camsur', 'Tagaytay', 'Mactan LG']
                        store_names = dept_names + hyp_names
                        dept_codes  = ['2001', '2002', '2093', '2004', '2005', '2006', '2007', '2009',
                                       '2015', '2016', '2017', '2018', '2019', '2020', '2223']
                        hyp_codes   = ['2008', '2010', '6001', '6003', '6004', '6005', '6006', '6009',
                                       '6010', '6013', '2015', '']
                        store_codes = dept_codes + hyp_codes

                        worksheet.merge_range(5, 15, 5, 15 + len(dept_names) - 1, 'Department Store', bold_fmt)
                        worksheet.merge_range(5, 15 + len(dept_names), 5, 15 + len(store_names) - 1, 'Hypermarket', bold_fmt)

                        row6 = (['NO', 'PRODUCT IMAGE', 'HIERARCHY', '', '', '', '', '', '', '', '', '', '', '', '']
                                + store_codes
                                + ['Total Qty Allocation', 'APPROVED', 'DISAPPROVED', 'ITEM CODES', '', ''])
                        row7 = (['', '', 'DEPT', 'CLASS', 'SUBCLASS', 'EAN-13 (if_available)', 'BRAND NAME',
                                 'ITEM DESCRIPTION ', 'STOCK/ PRODUCT CODE', 'COLOR', 'SIZE', 'MATERIAL/FABRIC',
                                 'REGULAR PRICE', 'SALE PRICE (promo item only)', 'STOCK AVAILABILITY']
                                + store_names
                                + ['Qty', '', '', 'SKU', 'UPC', 'MDSG REMARKS'])

                        worksheet.set_row(6, 45)
                        worksheet.set_row(7, 85)
                        for col_num in range(len(row6)):
                            val6 = row6[col_num]
                            val7 = row7[col_num]
                            is_store_col = 15 <= col_num < 15 + len(store_names)

                            if val6 == 'ITEM CODES' or val7 in ('SKU', 'UPC', 'MDSG REMARKS'):
                                fmt6 = fmt7 = red_hdr_fmt
                            elif is_store_col:
                                fmt6, fmt7 = hdr_fmt, rotate_hdr_fmt
                            else:
                                fmt6 = fmt7 = hdr_fmt

                            worksheet.write(6, col_num, val6, fmt6)
                            worksheet.write(7, col_num, val7, fmt7)

                            if col_num == 1:         worksheet.set_column(col_num, col_num, 15)
                            elif col_num == 7:       worksheet.set_column(col_num, col_num, 35)
                            elif col_num == 8:       worksheet.set_column(col_num, col_num, 18)
                            elif is_store_col:       worksheet.set_column(col_num, col_num, 5)
                            else:                    worksheet.set_column(col_num, col_num, 12)

                        worksheet.merge_range(6, 2, 6, 4, 'HIERARCHY', hdr_fmt)
                        item_code_start = 15 + len(store_names) + 3
                        worksheet.merge_range(6, item_code_start, 6, item_code_start + 2, 'ITEM CODES', red_hdr_fmt)

                    else:  # SM / default — blue theme
                        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#BDD7EE', 'border': 1, 'align': 'center'})
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(0, col_num, value, header_fmt)
                            if value != img_col_name:
                                if any(x in value for x in ('Desc', 'Name', 'Description')):
                                    worksheet.set_column(col_num, col_num, 45)
                                elif 'Brand' in value:
                                    worksheet.set_column(col_num, col_num, 20)
                                elif any(x in value for x in ('Size', 'Color', 'Price', 'Cost', 'Qty', 'Stock', 'UPC')):
                                    worksheet.set_column(col_num, col_num, 13)
                                else:
                                    worksheet.set_column(col_num, col_num, 18)

                    # 5. Image insertion
                    if chain_selection not in ('RDS', 'GCAP') and img_col_name and img_col_name in final_cols:
                        image_cache = build_image_cache(NETWORK_IMAGE_PATH)
                        img_col_idx = final_cols.index(img_col_name)
                        worksheet.set_column(img_col_idx, img_col_idx, 18)

                        for i, item_no in enumerate(bucket_df['Item No_']):
                            save_progress(req_id, i, len(bucket_df), f'Inserting Images: {item_no}')
                            row_idx = i + data_start_row
                            worksheet.set_row(row_idx, 90)
                            img_path = find_image_in_cache(image_cache, item_no)
                            if img_path:
                                try:
                                    with Image.open(img_path) as img:
                                        img_resized  = img.resize((120, 120), Image.Resampling.LANCZOS)
                                        img_byte_arr = io.BytesIO()
                                        img_resized.save(img_byte_arr, format='PNG')
                                        img_byte_arr.seek(0)
                                        worksheet.insert_image(
                                            row_idx, img_col_idx, f'{item_no}.png',
                                            {'image_data': img_byte_arr, 'object_position': 1},
                                        )
                                        images_found_count += 1
                                except Exception:
                                    worksheet.write(row_idx, img_col_idx, 'ERR')

                    # 6. Save (zip mode only)
                    if not is_multisheet_mode:
                        current_writer.close()
                        zip_file.writestr(filename, excel_output.getvalue())

                except Exception as exc:
                    logger.error('Brand bucket failed: %s', exc)

        except Exception as outer_exc:
            logger.error('Loop failure: %s', outer_exc)
        finally:
            if is_multisheet_mode and global_writer:
                global_writer.close()
            elif zip_file:
                zip_file.close()

        save_progress(req_id, len(merged_df), len(merged_df), 'Finalizing...')
        output_buffer.seek(0)

        if is_multisheet_mode:
            final_name   = f'{filename_base}.xlsx'
            mimetype_val = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
            mimetype_val = 'application/zip'
            if chain_selection in ('RDS', 'RUSTANS', 'GCAP', 'KCC', 'GGRAND', 'ALTURAS', 'METRO'):
                final_name = f'{filename_base}.zip'
            elif chain_selection in ('WATSONS', 'WATSONS ONLINE'):
                final_name = final_zip_name
            else:
                final_name = (
                    f'SM{datetime.now().strftime("%m%d%Y")}.zip'
                    if not final_zip_name or 'SC_TEMP' in final_zip_name
                    else final_zip_name
                )

        response = make_response(send_file(
            output_buffer, mimetype=mimetype_val,
            as_attachment=True, download_name=final_name,
        ))
        response.headers.update({
            'X-Filename':                    final_name,
            'X-Total-Items':                 str(len(merged_df)),
            'X-Images-Found':                str(images_found_count),
            'Access-Control-Expose-Headers': 'X-Filename, X-Total-Items, X-Images-Found',
        })
        return response

    except Exception as exc:
        logger.error('Global failure: %s', traceback.format_exc())
        return jsonify({'error': str(exc)}), 500
    finally:
        if conn:
            conn.close()


@transactions_bp.route('/transaction-generator')
def transaction_generator():
    if not session.get('sdr_loggedin'):
        return render_template('home.html')
    return render_template('transaction_form.html')
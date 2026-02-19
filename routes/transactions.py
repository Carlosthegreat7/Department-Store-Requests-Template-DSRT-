import os
import pandas as pd
import io
import json
import zipfile
import traceback
import logging
import mysql.connector
import time
import shutil
from datetime import datetime, timedelta
from PIL import Image
from flask import Blueprint, render_template, request, jsonify, session, send_file, make_response, Response

# 1. import new atc script
try:
    from .transactions_atc import process_atcrep_template
except ImportError:
    # fallback if the file isn't in the routes folder
    from transactions_atc import process_atcrep_template

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import SQLconnect from the portal package
try:
    from portal.SQLconnection import SQLconnect
except ImportError:
    try:
        from SQLconnection import SQLconnect
    except ImportError:
        SQLconnect = None

transactions_bp = Blueprint('transactions', __name__)

NETWORK_IMAGE_PATH = r'\\mgsvr03\catalog'

# --- PROGRESS TRACKING HELPER (FILE BASED) ---
# We use files instead of variables so this works even if the server uses multiple worker processes.

PROGRESS_DIR = os.path.join(os.getcwd(), 'temp_progress')
if not os.path.exists(PROGRESS_DIR):
    os.makedirs(PROGRESS_DIR)

def save_progress(req_id, current, total, status):
    """Writes progress to a JSON file accessible by all workers."""
    try:
        file_path = os.path.join(PROGRESS_DIR, f"{req_id}.json")
        data = {"current": current, "total": total, "status": status}
        with open(file_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to write progress: {e}")

def get_progress_data(req_id):
    """Reads progress from the JSON file."""
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
            host="localhost",
            user="root",
            password="",
            database="myproject"
        )
    except Exception:
        return None

def build_image_cache(base_path):
    cache = {}
    extensions = {'.jpg', '.JPG', '.jpeg', '.png'}
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

def find_image_in_cache(cache, item_no):
    item_no_lower = str(item_no).strip().lower()
    if not item_no_lower: return None
    first_char = item_no_lower[0]
    bucket = cache.get(first_char, [])
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
            # READ from file
            data = get_progress_data(req_id)
            
            yield f"data: {json.dumps(data)}\n\n"
            
            # Stop stream if finished
            if data["status"] == "Finalizing..." or (data["total"] > 0 and data["current"] >= data["total"]):
                # Optional: Clean up file logic could go here, or rely on OS cleanup
                break
            
            # Poll every 0.5 seconds to avoid server blocking
            time.sleep(0.5)
                
    return Response(generate(), mimetype='text/event-stream')

@transactions_bp.route('/verify-codes', methods=['POST'])
def verify_codes():
    pc_memo = request.form.get('pc_memo', '').strip().upper()
    sales_code = request.form.get('sales_code', '').strip().upper()
    company_selection = request.form.get('company', '').strip().upper()
    
    # logic to choose the correct DB
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

    # Use Sales Code as the Unique ID for this user session
    req_id = sales_code
    
    # Initialize Progress File
    save_progress(req_id, 0, 0, "Initializing...")

    # 2. REDIRECTION LOGIC
    if company_selection in ['ATC', 'TPC']:
        logger.info(f"Redirecting to ATC/TPC logic for company: {company_selection}")
        # Note: Ideally you pass save_progress to the ATC script, but for compatibility
        # we act as if it's external.
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
            price_qry = ('SELECT "Item No_", "Unit Price" AS SRP, "Discount Level" AS "Price_Discount" '
                         'FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
                         'WHERE "Sales Code"=? AND "PC Memo No"=?' )
            prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])
        except Exception as e:
            price_qry = ('SELECT "Item No_", "Unit Price" AS SRP '
                         'FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
                         'WHERE "Sales Code"=? AND "PC Memo No"=?' )
            prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])

        if prices_df.empty:
            return jsonify({"error": "No records found in Navision for the provided codes."}), 404

        item_list = prices_df['Item No_'].tolist()
        total_items_count = len(item_list)
        
        save_progress(req_id, 0, total_items_count, f"Found {total_items_count} items. Starting Retrieval...")

        # --- CHUNKING LOGIC ---
        chunk_size = 2000
        items_dfs = []

        # Iterate through item list in chunks to prevent query overflow
        for i in range(0, len(item_list), chunk_size):
            chunk = item_list[i:i + chunk_size]
            placeholders = ', '.join(['?'] * len(chunk))
            
            # Update Progress File
            save_progress(req_id, i, total_items_count, f"Retrieving item details... ({i}/{total_items_count})")

            # --- DYNAMIC DISCOUNT LEVEL FETCH (Item Table Fallback) ---
            try:
                item_qry = (f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                            f'"Vendor Item No_" AS "Style_Stockcode", "Net Weight", "Gross Weight", '
                            f'"Point_Power", "Base Unit of Measure" AS "Unit_of_Measure", '
                            f'"Dial Color", "Case _Frame Size", "Gender", "Item Category Code", '
                            f'"Discount Level" AS "Item_Discount" '
                            f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                            f'WHERE "No_" IN ({placeholders})')
                
                chunk_df = pd.read_sql(item_qry, conn, params=chunk)
            except Exception:
                item_qry = (f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                            f'"Vendor Item No_" AS "Style_Stockcode", "Net Weight", "Gross Weight", '
                            f'"Point_Power", "Base Unit of Measure" AS "Unit_of_Measure", '
                            f'"Dial Color", "Case _Frame Size", "Gender", "Item Category Code" '
                            f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                            f'WHERE "No_" IN ({placeholders})')
                
                chunk_df = pd.read_sql(item_qry, conn, params=chunk)
            
            items_dfs.append(chunk_df)
            
            # Tiny sleep to let other threads breathe
            time.sleep(0.01)

        # Combine all chunks
        if items_dfs:
            items_df = pd.concat(items_dfs, ignore_index=True)
        else:
            items_df = pd.DataFrame()

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
            for col in ['RCC SKU', 'IMAGE', 'BRAND CODE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'SIZE RUN', 'SET / PC', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 'MATERIAL', 'LINK TO HI-RES IMAGE']: merged_df[col] = ""
            final_cols = ['RCC SKU', 'IMAGE', 'VENDOR ITEM CODE', 'PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)', 'PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)', 'PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)', 'VENDOR CODE', 'BRAND CODE', 'RETAIL PRICE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'Dial Color', 'SIZE RUN', 'Case _Frame Size', 'SET / PC', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 'MATERIAL', 'LINK TO HI-RES IMAGE', 'Gender']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGE', "Rustans Template", 14, 15
        
        elif chain_selection == "GCAP":
            # 1. Basic Columns
            merged_df['brand'] = merged_df['Brand'].fillna('')
            merged_df['item code'] = merged_df['Item No_']
            
            # 2. Promo Category Logic (@ or # means PROMO)
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
            merged_df['description'] = merged_df['Description'].fillna('')
            merged_df['price'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            
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

        else:
            # SM / Default Logic
            merged_df['DESCRIPTION'] = (merged_df['Brand'].fillna('') + " " + merged_df['Description'].fillna('') + " " + merged_df['Dial Color'].fillna('') + " " + merged_df['Case _Frame Size'].fillna('') + " " + merged_df['Style_Stockcode'].fillna('')).str.replace(r'[^a-zA-Z0-9\s]', '', regex=True).str[:50]
            merged_df['COLOR'] = merged_df['Dial Color'].fillna(''); merged_df['SIZES'] = merged_df['Case _Frame Size'].fillna(''); merged_df['SRP'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            merged_df['EXP_DEL_MONTH'] = (time_now + timedelta(days=30)).strftime('%m/%d/%Y')
            merged_df['SOURCE_MARKED'] = ""; merged_df['REMARKS'] = ""; merged_df['ONLINE ITEMS'] = "NO"
            merged_df['PACKAGE WEIGHT IN KG'] = merged_df['Gross Weight']; merged_df['PRODUCT WEIGHT IN KG'] = merged_df['Net Weight']
            for d in ['PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM']: merged_df[d] = "-"
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
                        if chain_selection in ["RDS", "GCAP", "KCC"]:
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

                        # [FIX] Check for Duplicate Filenames
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
                        safe_sheet = (str(brand_name).replace('/', '-').replace('\\', '-').replace('?', '').replace('*', '').replace('[', '').replace(']', '').replace(':', ''))[:31]
                        current_writer = global_writer
                        current_sheet_name = safe_sheet
                        data_start_row = 12 
                    else:
                        excel_output = io.BytesIO()
                        current_writer = pd.ExcelWriter(excel_output, engine='xlsxwriter')
                        current_sheet_name = sheet_name_val
                        data_start_row = 2 if chain_selection == "RDS" else (6 if chain_selection == "KCC" else 1)

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
                    # Note: We updated this condition so KCC gets images since it has a "sample image" column!
                    if chain_selection not in ["RDS", "GCAP"] and img_col_name in final_cols:
                        image_cache = build_image_cache(NETWORK_IMAGE_PATH)
                        img_col_idx = final_cols.index(img_col_name)
                        worksheet.set_column(img_col_idx, img_col_idx, 35) 
                        
                        for i, item_no in enumerate(bucket_df['Item No_']):
                            # Update global progress count
                            # Note: To be perfectly accurate we should track a global index, but simplistic update is fine for UI
                            save_progress(req_id, i, len(bucket_df), f"Inserting Images: {item_no}")
                            
                            row_idx = i + data_start_row
                            worksheet.set_row(row_idx, 180)
                            
                            img_path = find_image_in_cache(image_cache, item_no)
                            if img_path:
                                try:
                                    with Image.open(img_path) as img:
                                        img_resized = img.resize((240, 240), Image.Resampling.LANCZOS)
                                        img_byte_arr = io.BytesIO()
                                        img_resized.save(img_byte_arr, format='PNG')
                                        img_byte_arr.seek(0)
                                        worksheet.insert_image(row_idx, img_col_idx, f"{item_no}.png", {'image_data': img_byte_arr, 'object_position': 1})
                                        images_found_count += 1
                                except: worksheet.write(row_idx, img_col_idx, "ERR")
                    
                    # 6. Save (If in Zip Mode)
                    if not is_multisheet_mode:
                        current_writer.close()
                        excel_output.seek(0)
                        zip_file.writestr(filename, excel_output.read())

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
             if chain_selection in ["RDS", "RUSTANS", "GCAP", "KCC"]: final_name = f"{filename_base}.zip"
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
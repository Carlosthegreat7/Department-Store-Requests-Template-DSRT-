import os
import pandas as pd
import io
import json
import zipfile
import calendar
import traceback
import logging
import mysql.connector
from datetime import datetime, timedelta
from PIL import Image
from flask import Blueprint, render_template, request, jsonify, session, flash, redirect, url_for, send_file, make_response, Response

# 1. IMPORT THE NEW ATC SCRIPT
try:
    from .transactions_atc import process_atcrep_template
except ImportError:
    # Fallback if the file isn't in the routes folder
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

progress_data = {"current": 0, "total": 0, "status": "Initializing..."}
NETWORK_IMAGE_PATH = r'\\mgsvr09\niccatalog'

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
    def generate():
        while True:
            yield f"data: {json.dumps(progress_data)}\n\n"
            if progress_data["current"] >= progress_data["total"] and progress_data["total"] > 0 and progress_data["status"] != "Initializing...":
                break
    return Response(generate(), mimetype='text/event-stream')

@transactions_bp.route('/verify-codes', methods=['POST'])
def verify_codes():
    pc_memo = request.form.get('pc_memo', '').strip().upper()
    sales_code = request.form.get('sales_code', '').strip().upper()
    company_selection = request.form.get('company', '').strip().upper() # New line
    
    # Logic to choose the correct DB
    is_atcrep = company_selection in ['ATC', 'TPC']
    db_target = 'ATCREP' if is_atcrep else 'NICREP'
    
    # note: navision table names often require underscores
    if company_selection == 'ATC':
        table_prefix = 'About Time Corporation' 
    elif company_selection == 'TPC':
        table_prefix = 'Transcend Prime Inc'
    else:
        table_prefix = 'Newtrends International Corp_'

    conn = None 
    try:
        # Connect to the target database (NICREP or ATCREP)
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
    global progress_data
    
    # 1. Get Selections
    chain_selection = request.form.get('chain', '').strip().upper()
    company_selection = request.form.get('company', '').strip().upper()
    pc_memo = request.form.get('pc_memo', '').strip().upper()
    sales_code = request.form.get('sales_code', '').strip().upper()

    # 2. REDIRECTION LOGIC: If company is ATC or TPC, use process_atcrep_template
    if company_selection in ['ATC', 'TPC']:
        logger.info(f"Redirecting to ATC/TPC logic for company: {company_selection}")
        return process_atcrep_template(
            chain_selection, company_selection, pc_memo, sales_code, 
            SQLconnect, get_mysql_conn, build_image_cache, 
            find_image_in_cache, NETWORK_IMAGE_PATH, progress_data
        )

    # 3. NIC SCRIPT LOGIC
    progress_data.update({"current": 0, "total": 0, "status": "Accessing NICREP..."})
    conn = None
    try:
        conn, cursor, prefix = SQLconnect('NICREP', "DSRT")
        if conn is None:
            return jsonify({"error": "Database Connection Failed"}), 500

        price_qry = ('SELECT "Item No_", "Unit Price" AS SRP '
                     'FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
                     'WHERE "Sales Code"=? AND "PC Memo No"=?' )
        prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])

        if prices_df.empty:
            return jsonify({"error": "No records found in Navision for the provided codes."}), 404

        item_list = prices_df['Item No_'].tolist()
        placeholders = ', '.join(['?'] * len(item_list))
        
        item_qry = (f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                    f'"Vendor Item No_" AS "Style_Stockcode", "Net Weight", "Gross Weight", '
                    f'"Point_Power", "Base Unit of Measure" AS "Unit_of_Measure", '
                    f'"Dial Color", "Case _Frame Size", "Gender" '
                    f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                    f'WHERE "No_" IN ({placeholders})')
        items_df = pd.read_sql(item_qry, conn, params=item_list)
        merged_df = pd.merge(items_df, prices_df, on="Item No_")

        # --- DYNAMIC VENDOR & BRAND LOOKUP (MYSQL) ---
        mysql_conn = get_mysql_conn()
        vendor_code, dynamic_mfg_no, brand_meta_map = "000000", "", {}
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

                unique_brands = merged_df['Brand'].unique().tolist()
                if unique_brands:
                    placeholders_sql = ', '.join(['%s'] * len(unique_brands))
                    h_df = pd.read_sql(f"SELECT b.product_group as Brand, b.dept_code, b.sub_dept_code, b.class_code, s.subclass_code FROM brands b LEFT JOIN sub_classes s ON b.product_group = s.product_group WHERE b.product_group IN ({placeholders_sql})", mysql_conn, params=unique_brands)
                    for _, row in h_df.iterrows():
                        brand_meta_map[row['Brand']] = {'dept_sub': f"{row['dept_code']}{row['sub_dept_code']}", 'class_sub': f"{row['class_code']}{row['subclass_code']}"}
            finally:
                mysql_conn.close()

        # --- 4. DATA MAPPING ---
        time_now = datetime.now()
        date_str = time_now.strftime('%m-%d-%Y')
        filename_base = f"{chain_selection} {company_selection} {date_str}"
        
        if chain_selection == "RDS":
            # Map Fields
            merged_df['SKU Number'] = ""; merged_df['SKU Number with check digit'] = ""; merged_df['Sku Number_dup'] = ""
            merged_df['Item Description'] = merged_df['Description'].fillna('').str[:30]
            merged_df['Short name'] = merged_df['Description'].fillna('').str[:10]
            merged_df['Item Status'] = "A"; merged_df['Buyer'] = "B92"; merged_df['W/SCD 5% DISC'] = "N"; merged_df['Inventory Grp'] = ""; merged_df['W/PWD 5% DISC'] = "N"
            merged_df['SKU Type'] = ""; merged_df['Merchandiser'] = ""; merged_df['POS Tax Code'] = "V"
            merged_df['Primary Vendor'] = vendor_code; merged_df['Ship Pt'] = ""; merged_df['Manufacturer'] = ""
            merged_df['Vendor Part#'] = ""; merged_df['Manufacturer Part#'] = dynamic_mfg_no
            merged_df['Dept'] = ""; merged_df['Sub-Dept'] = ""; merged_df['Class-'] = ""; merged_df['Sub-Class'] = ""
            merged_df['Product Code'] = ""; merged_df['TYPE'] = ""; merged_df['Primary Buy UPC'] = ""; merged_df['Saleable UPC'] = ""
            merged_df['Competitive Priced'] = ""; merged_df['Display on Web'] = ""; merged_df['Competitive Price'] = ""; merged_df['POS Price Prompt'] = ""
            merged_df['Original Price'] = merged_df['SRP'].fillna(0).map('{:.2f}'.format)
            merged_df['Prevent POS Download'] = "N"; merged_df['Next Regular Retail'] = ""; merged_df['Effective'] = ""; merged_df['Current Vendor Cost'] = ""; merged_df['Buying U/M'] = ""; merged_df['Selling U/M'] = ""
            merged_df['Standard Pack'] = "1"; merged_df['Minimum (Inner) Pack'] = "1"
            merged_df['Coordinate Group'] = "RDS"; merged_df['Super Brand'] = ""; merged_df['Brand_Col'] = merged_df['Brand']
            merged_df['Buy Code(C/S)'] = "S"; merged_df['Season'] = "NA"; merged_df['Set Code'] = "0"; merged_df['Mfg. No.'] = ""
            merged_df['Age Code'] = ""; merged_df['Label'] = ""; merged_df['Origin'] = ""; merged_df['Tag'] = ""; merged_df['Fair Event'] = ""; merged_df['Blank Event'] = ""
            merged_df['Price Point'] = merged_df['Point_Power'].fillna('')
            merged_df['Merchandise Flag'] = ""; merged_df['Hold Wholesale Order'] = "N"
            merged_df['Size'] = merged_df['Case _Frame Size'].fillna(''); merged_df['Substitute SKU'] = ""; merged_df['Core SKU'] = ""; merged_df['Replacement SKU'] = ""
            merged_df['Replenishment Code'] = "0"; merged_df['Sales $'] = ""; merged_df['Distribution Method'] = ""; merged_df['Sales Units'] = ""; merged_df['Rpl Start Date'] = ""
            merged_df['Gross Margin'] = ""; merged_df['Rpl End Date'] = ""; merged_df['User Defined'] = ""; merged_df['Avg. Model Stock'] = ""; merged_df['Avg. Order at'] = ""; merged_df['Maximum Stock'] = ""; merged_df['Display Minimum'] = ""; merged_df['Stock in Mult. of'] = ""; merged_df['Minimum Rpl Qty'] = "1"; merged_df['Item Profile'] = ""; merged_df['Hold Order'] = "N"; merged_df['Plan Lead Time'] = ""

            # Defined RDS Groupings with Gaps
            p1 = ['SKU Number', 'SKU Number with check digit', 'Sku Number_dup', 'Item Description', 'Short name', 'Item Status', 'Buyer', 'W/SCD 5% DISC', 'Inventory Grp', 'W/PWD 5% DISC', 'SKU Type', 'Merchandiser', 'POS Tax Code']
            p1_cont = ['Primary Vendor', 'Ship Pt', 'Manufacturer', 'Vendor Part#', 'Manufacturer Part#', 'Dept', 'Sub-Dept', 'Class-', 'Sub-Class']
            p2_3 = ['Product Code', 'TYPE', 'Primary Buy UPC', 'Saleable UPC', 'Competitive Priced', 'Display on Web', 'Competitive Price', 'POS Price Prompt', 'Original Price', 'Prevent POS Download', 'Next Regular Retail', 'Effective', 'Current Vendor Cost', 'Buying U/M', 'Selling U/M', 'Standard Pack', 'Minimum (Inner) Pack']
            p4 = ['Coordinate Group', 'Super Brand', 'Brand_Col', 'Buy Code(C/S)', 'Season', 'Set Code', 'Mfg. No.', 'Age Code', 'Label', 'Origin', 'Tag', 'Fair Event', 'Blank Event', 'Price Point', 'Merchandise Flag', 'Hold Wholesale Order', 'Size', 'Substitute SKU', 'Core SKU', 'Replacement SKU']
            p5 = ['Replenishment Code', 'Sales $', 'Distribution Method', 'Sales Units', 'Rpl Start Date', 'Gross Margin', 'Rpl End Date', 'User Defined', 'Avg. Model Stock', 'Avg. Order at', 'Maximum Stock', 'Display Minimum', 'Stock in Mult. of', 'Minimum Rpl Qty', 'Item Profile', 'Hold Order', 'Plan Lead Time']
            
            # Construct Final RDS Layout with Gaps
            rds_layout = []
            for i, group in enumerate([p1, p1_cont, p2_3, p4, p5]):
                rds_layout.extend(group)
                if i < 4: rds_layout.append(f'GAP_{i}') # Insert Gap identifier
            
            for i in range(4): merged_df[f'GAP_{i}'] = ""
            final_cols, sheet_name_val, header_row_idx, data_start_row = rds_layout, "TEMPLATE", 1, 2
        
        elif chain_selection == "RUSTANS":
            # Rustans mapping
            merged_df['VENDOR ITEM CODE'] = merged_df['Item No_']; merged_df['VENDOR CODE'] = vendor_code
            combined_desc_str = (merged_df['Description'].fillna('') + " " + merged_df['Dial Color'].fillna('') + " " + merged_df['Style_Stockcode'].fillna('') + " " + merged_df['Brand'].fillna('')).str.strip()
            merged_df['PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)'] = combined_desc_str.str[:30]
            merged_df['PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)'] = merged_df['Description'].fillna('').str[:10]
            merged_df['PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)'] = combined_desc_str.str[:50]
            merged_df['RETAIL PRICE'] = merged_df['SRP'].fillna(0).apply(lambda x: '{:.2f}'.format(x)) 
            for col in ['RCC SKU', 'IMAGE', 'BRAND CODE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'SIZE RUN', 'SET / PC', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 'MATERIAL', 'LINK TO HI-RES IMAGE']: merged_df[col] = ""
            final_cols = ['RCC SKU', 'IMAGE', 'VENDOR ITEM CODE', 'PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)', 'PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)', 'PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)', 'VENDOR CODE', 'BRAND CODE', 'RETAIL PRICE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'Dial Color', 'SIZE RUN', 'Case _Frame Size', 'SET / PC', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 'MATERIAL', 'LINK TO HI-RES IMAGE', 'Gender']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGE', "Rustans Template", 14, 15
        
        else:
            # Default logic
            merged_df['DESCRIPTION'] = (merged_df['Brand'].fillna('') + " " + merged_df['Description'].fillna('') + " " + merged_df['Dial Color'].fillna('') + " " + merged_df['Case _Frame Size'].fillna('') + " " + merged_df['Style_Stockcode'].fillna('')).str.replace(r'[^a-zA-Z0-9\s]', '', regex=True).str[:50]
            merged_df['SRP_FMT'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format); merged_df['IMAGES'] = ""
            final_cols = ['DESCRIPTION', 'Dial Color', 'Case _Frame Size', 'Style_Stockcode', 'SRP_FMT', 'Unit_of_Measure', 'IMAGES']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGES', "Template", 0, 1

        # --- 5. EXCEL GENERATION ---
        zip_output = io.BytesIO()
        with zipfile.ZipFile(zip_output, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for brand_name, bucket_df in merged_df.groupby('Brand'):
                try:
                    filename = f"{filename_base} - {brand_name}.xlsx"
                    excel_output = io.BytesIO()
                    with pd.ExcelWriter(excel_output, engine='xlsxwriter') as writer:
                        bucket_df[final_cols].to_excel(writer, sheet_name=sheet_name_val, index=False, startrow=data_start_row, header=False)
                        workbook, worksheet = writer.book, writer.sheets[sheet_name_val]
                        
                        # Formatting Themes
                        f_p1 = workbook.add_format({'bold': True, 'bg_color': '#BDD7EE', 'border': 1, 'align': 'center'})
                        f_p1c = workbook.add_format({'bold': True, 'bg_color': '#E2EFDA', 'border': 1, 'align': 'center'})
                        f_p23 = workbook.add_format({'bold': True, 'bg_color': '#FFF2CC', 'border': 1, 'align': 'center'})
                        f_p4 = workbook.add_format({'bold': True, 'bg_color': '#EAD1DC', 'border': 1, 'align': 'center'})
                        f_p5 = workbook.add_format({'bold': True, 'bg_color': '#FCE4D6', 'border': 1, 'align': 'center'})
                        f_gap = workbook.add_format({'bg_color': '#FFFFFF', 'border': 0})
                        f_page_hdr = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1, 'align': 'center', 'font_size': 11})
                        
                        if chain_selection == "RDS":
                            # Set Column Widths
                            worksheet.set_column('A:C', 12); worksheet.set_column('D:D', 35); worksheet.set_column('E:M', 10)
                            worksheet.set_column('N:N', 3); worksheet.set_column('O:W', 12); worksheet.set_column('X:X', 3)
                            
                            # Write Page Headers (Row 0) and Field Headers (Row 1)
                            curr_col = 0
                            for i, (group, fmt, label) in enumerate([(p1, f_p1, 'Page 1 - General Info'), (p1_cont, f_p1c, 'Page 1 (Cont)'), (p2_3, f_p23, 'Page 2 & 3 - Price/UPC'), (p4, f_p4, 'Page 4 - Brand/Size'), (p5, f_p5, 'Page 5 - Replenishment')]):
                                start_col = curr_col
                                # Write Page Label
                                worksheet.merge_range(0, start_col, 0, start_col + len(group) - 1, label, f_page_hdr)
                                # Write Field Headers
                                for field in group:
                                    worksheet.write(1, curr_col, field, fmt)
                                    curr_col += 1
                                # Handle Gap
                                if i < 4:
                                    worksheet.set_column(curr_col, curr_col, 2)
                                    worksheet.write(1, curr_col, "", f_gap)
                                    curr_col += 1
                        else:
                            # Standard header
                            header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1, 'align': 'center'})
                            for col_num, value in enumerate(final_cols): 
                                worksheet.write(header_row_idx, col_num, value, header_fmt)

                        # Rustans/Default Image Insertion
                        if chain_selection != "RDS" and img_col_name in final_cols:
                            image_cache = build_image_cache(NETWORK_IMAGE_PATH)
                            img_col_idx = final_cols.index(img_col_name); worksheet.set_column(img_col_idx, img_col_idx, 35)
                            for i, item_no in enumerate(bucket_df['Item No_']):
                                worksheet.set_row(i + data_start_row, 180)
                                img_path = find_image_in_cache(image_cache, item_no)
                                if img_path:
                                    try:
                                        with Image.open(img_path) as img:
                                            img_resized = img.resize((240, 240), Image.Resampling.LANCZOS)
                                            img_byte_arr = io.BytesIO(); img_resized.save(img_byte_arr, format='PNG'); img_byte_arr.seek(0)
                                        worksheet.insert_image(i + data_start_row, img_col_idx, f"{item_no}.png", {'image_data': img_byte_arr, 'object_position': 1})
                                    except: worksheet.write(i + data_start_row, img_col_idx, "CORRUPT")

                    excel_output.seek(0); zip_file.writestr(filename, excel_output.read())
                except Exception as e: logger.error(f"Brand bucket failed: {e}")

        zip_output.seek(0)
        final_zip_name = f"{filename_base}.zip"
        response = make_response(send_file(zip_output, mimetype='application/zip', as_attachment=True, download_name=final_zip_name))
        response.headers.update({'X-Filename': final_zip_name, 'Access-Control-Expose-Headers': 'X-Filename'})
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
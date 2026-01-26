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

def find_image_recursive(base_path, item_no):
    extensions = ['.jpg', '.JPG', '.jpeg', '.png']
    item_no_lower = str(item_no).strip().lower()
    try:
        for root, dirs, files in os.walk(base_path):
            for filename in files:
                name_part = os.path.splitext(filename)[0].lower()
                ext_part = os.path.splitext(filename)[1].lower()
                if name_part.startswith(item_no_lower) and ext_part in extensions:
                    return os.path.join(root, filename)
    except Exception as e:
        logger.error(f"Directory access error: {e}")
    return None

@transactions_bp.route('/progress')
def progress():
    def generate():
        while True:
            yield f"data: {json.dumps(progress_data)}\n\n"
            if progress_data["current"] >= progress_data["total"] and progress_data["total"] > 0:
                break
    return Response(generate(), mimetype='text/event-stream')

@transactions_bp.route('/verify-codes', methods=['POST'])
def verify_codes():
    pc_memo = request.form.get('pc_memo', '').strip().upper()
    sales_code = request.form.get('sales_code', '').strip().upper()
    
    conn = None 
    try:
        conn, cursor, prefix = SQLconnect('NICREP', "DSRT")
        if conn is None:
            return jsonify({"success": False, "error": "Database Connection Failed"}), 500

        check_qry = (
            'SELECT COUNT(*) as cnt '
            'FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
            'WHERE "Sales Code"=? AND "PC Memo No"=?'
        )
        cursor.execute(check_qry, (sales_code, pc_memo))
        result = cursor.fetchone()
        
        if result and result[0] > 0:
            return jsonify({"success": True, "count": result[0]})
        else:
            return jsonify({"success": False, "error": "No records found for these codes."})
    except Exception as e:
        logger.error(f"Verification Error: {str(e)}")
        return jsonify({"success": False, "error": str(e)})
    finally:
        if conn: conn.close()

@transactions_bp.route('/process-template', methods=['POST'])
def process_template():
    global progress_data
    progress_data.update({"current": 0, "total": 0, "status": "Accessing Navision SQL..."})

    chain_selection = request.form.get('chain', '').strip().upper()
    company_selection = request.form.get('company', '').strip().upper()
    pc_memo = request.form.get('pc_memo', '').strip().upper()
    sales_code = request.form.get('sales_code', '').strip().upper()
    
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
                    f'"Pricepoint" AS "Pricepoint_SKU", "Base Unit of Measure" AS "Unit_of_Measure", '
                    f'"Dial Color", "Case _Frame Size", "Gender" '
                    f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                    f'WHERE "No_" IN ({placeholders})')
        items_df = pd.read_sql(item_qry, conn, params=item_list)
        merged_df = pd.merge(items_df, prices_df, on="Item No_")

        if len(merged_df) > 5000:
            return jsonify({"error": "Request too large. Please limit to 5,000 items per batch."}), 400

        # --- DYNAMIC COLUMN LOGIC ---
        if chain_selection == "RUSTANS":
            vendor_code_val = "703921" if company_selection == "NIC" else "000000"
            
            merged_df['RCC SKU'] = ""; merged_df['IMAGE'] = ""
            merged_df['VENDOR ITEM CODE'] = merged_df['Item No_']
            
            # REVISION: Concatenate Description, Color, Vendor Item No (Style_Stockcode), and Brand
            combined_desc_str = (
                merged_df['Description'].fillna('') + " " + 
                merged_df['Dial Color'].fillna('') + " " + 
                merged_df['Style_Stockcode'].fillna('') + " " + 
                merged_df['Brand'].fillna('')
            ).str.strip()

            merged_df['PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)'] = combined_desc_str.str[:30]
            merged_df['PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)'] = merged_df['Description'].fillna('').str[:10]
            merged_df['PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)'] = combined_desc_str.str[:50]
            
            merged_df['VENDOR CODE'] = vendor_code_val
            merged_df['BRAND CODE'] = ""
            merged_df['RETAIL PRICE'] = merged_df['SRP'].fillna(0).apply(lambda x: '{:.2f}'.format(x)) 
            merged_df['COLOR'] = merged_df['Dial Color'].fillna('')
            merged_df['SIZE'] = merged_df['Case _Frame Size'].fillna('')
            merged_df['Gender'] = merged_df['Gender'].fillna('')

            placeholders_list = [
                'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 
                'SEASON CODE', 'THEME', 'COLLECTION', 'SIZE RUN', 'SET / PC', 'MAKATI', 
                'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 
                'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 
                'MATERIAL', 'LINK TO HI-RES IMAGE'
            ]
            for col in placeholders_list: merged_df[col] = ""

            final_cols = [
                'RCC SKU', 'IMAGE', 'VENDOR ITEM CODE', 'PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)', 
                'PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)', 'PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)', 
                'VENDOR CODE', 'BRAND CODE', 'RETAIL PRICE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 
                'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'COLOR', 
                'SIZE RUN', 'SIZE', 'SET / PC', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 
                'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 
                'PRODUCT & CARE DETAILS', 'MATERIAL', 'LINK TO HI-RES IMAGE', 'Gender'
            ]
            img_col_name, sheet_name_val = 'IMAGE', "Rustans Template"
        else:
            # Standard SM Logic
            merged_df['DESCRIPTION'] = (
                merged_df['Brand'].fillna('') + " " + merged_df['Description'].fillna('') + " " + 
                merged_df['Dial Color'].fillna('') + " " + merged_df['Case _Frame Size'].fillna('') + " " + 
                merged_df['Style_Stockcode'].fillna('')
            ).str.replace(r'[^a-zA-Z0-9\s]', '', regex=True).str[:50]
            merged_df['COLOR'] = merged_df['Dial Color'].fillna('')
            merged_df['SIZES'] = merged_df['Case _Frame Size'].fillna('')
            merged_df['SRP_FMT'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            
            now = datetime.now()
            month = (now.month % 12) + 1
            year = now.year + (1 if now.month == 12 else 0)
            day = min(now.day, calendar.monthrange(year, month)[1])
            merged_df['EXP_DEL_MONTH'] = datetime(year, month, day).strftime('%m/%d/%Y')

            merged_df['SOURCE_MARKED'] = ""; merged_df['REMARKS'] = ""; merged_df['ONLINE ITEMS'] = "NO"
            merged_df['PACKAGE WEIGHT IN KG'] = merged_df['Gross Weight']
            merged_df['PRODUCT WEIGHT IN KG'] = merged_df['Net Weight']
            for dim_col in ['PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 
                            'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM']:
                merged_df[dim_col] = "-"
            merged_df['IMAGES'] = ""

            final_cols = [
                'DESCRIPTION', 'COLOR', 'SIZES', 'Style_Stockcode', 'SOURCE_MARKED', 
                'SRP_FMT', 'Unit_of_Measure', 'EXP_DEL_MONTH', 'REMARKS', 'Pricepoint_SKU', 
                'IMAGES', 'ONLINE ITEMS', 'PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 
                'PACKAGE HEIGHT IN CM', 'PACKAGE WEIGHT IN KG', 'PRODUCT LENGTH IN CM', 
                'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM', 'PRODUCT WEIGHT IN KG'
            ]
            img_col_name, sheet_name_val = 'IMAGES', "Template"

        # --- VENDOR CODE & BRAND MAPPING ---
        mysql_conn = get_mysql_conn()
        vendor_code, brand_meta_map = "000000", {}
        if mysql_conn:
            try:
                if company_selection == "NIC":
                    vendor_code = "703921" if chain_selection == "RUSTANS" else "144011"
                else:
                    vendor_map = {"ATC": "ABOUT TIME CORP.", "TPC": "TIME PLUS CORP."}
                    v_df = pd.read_sql("SELECT vendor_code FROM vendors WHERE vendor_name = %s LIMIT 1", 
                                       mysql_conn, params=[vendor_map.get(company_selection, "")])
                    if not v_df.empty: vendor_code = str(v_df['vendor_code'].iloc[0])

                unique_brands = merged_df['Brand'].unique().tolist()
                if unique_brands:
                    placeholders_sql = ', '.join(['%s'] * len(unique_brands))
                    h_qry = f"""
                        SELECT b.product_group as Brand, b.dept_code, b.sub_dept_code, b.class_code, s.subclass_code 
                        FROM brands b LEFT JOIN sub_classes s ON b.product_group = s.product_group 
                        WHERE b.product_group IN ({placeholders_sql})
                    """
                    h_df = pd.read_sql(h_qry, mysql_conn, params=unique_brands)
                    for _, row in h_df.iterrows():
                        brand_meta_map[row['Brand']] = {
                            'dept_sub': f"{row['dept_code']}{row['sub_dept_code']}",
                            'class_sub': f"{row['class_code']}{row['subclass_code']}"
                        }
            finally:
                mysql_conn.close()

        # --- PACKAGING AND ZIP GENERATION ---
        progress_data["total"] = len(merged_df)
        time_stamp = datetime.now().strftime('%m%d%H%M')
        zip_output = io.BytesIO()
        total_found_images, processed_count = 0, 0

        with zipfile.ZipFile(zip_output, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for brand_name, bucket_df in merged_df.groupby('Brand'):
                try:
                    meta = brand_meta_map.get(brand_name, {'dept_sub': '000000', 'class_sub': '000000'})
                    filename = f"RUSTANS_{sales_code}_{time_stamp}.xlsx" if chain_selection == "RUSTANS" else f"SC{vendor_code}_{meta['dept_sub']}_{meta['class_sub']}_{time_stamp}.xlsx"
                    
                    excel_output = io.BytesIO()
                    COL_WIDTH_PX = 240
                    ROW_HEIGHT_PT = 180
                    ROW_HEIGHT_PX = int(ROW_HEIGHT_PT / 0.75) 

                    with pd.ExcelWriter(excel_output, engine='xlsxwriter') as writer:
                        bucket_df[final_cols].to_excel(writer, sheet_name=sheet_name_val, index=False, startrow=1)
                        workbook, worksheet = writer.book, writer.sheets[sheet_name_val]
                        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
                        
                        for col_num, value in enumerate(final_cols):
                            worksheet.write(1, col_num, value, header_fmt)
                        
                        img_col_idx = final_cols.index(img_col_name)
                        worksheet.set_column(img_col_idx, img_col_idx, 35)

                        for i, item_no in enumerate(bucket_df['Item No_']):
                            processed_count += 1
                            progress_data.update({"current": processed_count, "status": f"Processing {brand_name}: {item_no}"})
                            worksheet.set_row(i + 2, ROW_HEIGHT_PT) 
                            
                            img_path = find_image_recursive(NETWORK_IMAGE_PATH, item_no)
                            if img_path:
                                try:
                                    # STRETCH REVISION: Physically resize image data using PIL to fill cell
                                    with Image.open(img_path) as img:
                                        img_resized = img.resize((COL_WIDTH_PX, ROW_HEIGHT_PX), Image.Resampling.LANCZOS)
                                        img_byte_arr = io.BytesIO()
                                        img_resized.save(img_byte_arr, format='PNG')
                                        img_byte_arr.seek(0)

                                    worksheet.insert_image(i + 2, img_col_idx, f"{item_no}.png", {
                                        'image_data': img_byte_arr,
                                        'x_offset': 0, 
                                        'y_offset': 0,
                                        'object_position': 1
                                    })
                                    total_found_images += 1
                                except Exception as e:
                                    logger.error(f"Resize failed for {item_no}: {e}")
                                    worksheet.write(i + 2, img_col_idx, "CORRUPT IMAGE")
                            else:
                                worksheet.write(i + 2, img_col_idx, "IMAGE NOT FOUND")

                    excel_output.seek(0)
                    zip_file.writestr(filename, excel_output.read())
                except Exception as e:
                    logger.error(f"Brand bucket failed: {e}")
                    continue

        zip_output.seek(0)
        zip_time = datetime.now().strftime('%m%d%H%M')
        zip_filename = f"RST_{vendor_code}_{zip_time}.zip" if chain_selection == "RUSTANS" else f"SC_{vendor_code}_{zip_time}.zip"
            
        response = make_response(send_file(zip_output, mimetype='application/zip', as_attachment=True, download_name=zip_filename))
        response.headers.update({
            'X-Total-Items': str(progress_data["total"]),
            'X-Images-Found': str(total_found_images),
            'X-Filename': zip_filename,
            'Access-Control-Expose-Headers': 'X-Total-Items, X-Images-Found, X-Filename'
        })
        return response

    except Exception as e:
        logger.error(f"Global Failure: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@transactions_bp.route('/transaction-generator')
def transaction_generator():
    if not session.get('sdr_loggedin'):
        return render_template('home.html')
    return render_template('transaction_form.html')
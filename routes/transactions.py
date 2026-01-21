import os
import pandas as pd
import io
import json
import calendar
import traceback
import mysql.connector
from datetime import datetime, timedelta
from PIL import Image
from flask import Blueprint, render_template, request, jsonify, session, flash, redirect, url_for, send_file, make_response, Response

# Import SQLconnect from the portal package
try:
    from portal.SQLconnection import SQLconnect
except ImportError:
    try:
        from SQLconnection import SQLconnect
    except ImportError:
        SQLconnect = None

transactions_bp = Blueprint('transactions', __name__)

# Global progress tracker for the animated loading bar
progress_data = {"current": 0, "total": 0, "status": "Initializing..."}

# Official network path for product images
NETWORK_IMAGE_PATH = r'\\mgsvr09\niccatalog'

def get_mysql_conn():
    """Safety-wrapped MySQL connection for the local hierarchy database."""
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
    """Searches through all subfolders for a file matching the item number."""
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
        print(f"Directory access error: {e}")
    return None

@transactions_bp.route('/progress')
def progress():
    """Streams real-time progress data to the frontend loading bar."""
    def generate():
        while True:
            yield f"data: {json.dumps(progress_data)}\n\n"
            if progress_data["current"] >= progress_data["total"] and progress_data["total"] > 0:
                break
    return Response(generate(), mimetype='text/event-stream')

@transactions_bp.route('/validate-request', methods=['POST'])
def validate_request():
    """Performs a case-insensitive check against the Price table for the form feedback."""
    pc_memo = request.form.get('pc_memo', '').strip().upper()
    sales_code = request.form.get('sales_code', '').strip().upper()
    
    if not pc_memo or not sales_code:
        return jsonify({'exists': False})

    conn, cursor, prefix = SQLconnect('NICREP', "DSRT")
    if conn is None:
        return jsonify({'exists': False, 'error': 'Database Connection Failed'})

    try:
        check_qry = ('SELECT TOP 1 "Item No_" FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
                     'WHERE "Sales Code"=? AND "PC Memo No"=?' )
        df = pd.read_sql(check_qry, conn, params=[sales_code, pc_memo])
        return jsonify({'exists': not df.empty})
    except Exception as e:
        return jsonify({'exists': False, 'error': str(e)})
    finally:
        if conn: conn.close()

@transactions_bp.route('/transaction-generator')
def transaction_generator():
    if not session.get('sdr_loggedin'):
        return render_template('home.html')
    return render_template('transaction_form.html')

@transactions_bp.route('/process-template', methods=['POST'])
def process_template():
    global progress_data
    
    # Reset progress
    progress_data["current"] = 0
    progress_data["total"] = 0
    progress_data["status"] = "Accessing Navision SQL..."

    company_selection = request.form.get('company', '').strip().upper()
    pc_memo = request.form.get('pc_memo').strip().upper()
    sales_code = request.form.get('sales_code').strip().upper()
    
    conn, cursor, prefix = SQLconnect('NICREP', "DSRT")
    if conn is None:
        return "Database Connection Failed", 500

    try:
        # 1. Fetch Price Data
        price_qry = ('SELECT "Item No_", "Unit Price" AS SRP '
                     'FROM dbo."Newtrends International Corp_$Sales Price" WITH (NOLOCK) '
                     'WHERE "Sales Code"=? AND "PC Memo No"=?' )
        prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])

        if prices_df.empty:
            return "No records found", 404

        # 2. Fetch Extended Item Details
        item_list = prices_df['Item No_'].tolist()
        placeholders = ', '.join(['?'] * len(item_list))
        item_qry = (f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                    f'"Vendor Item No_" AS "Style_Stockcode", "Net Weight", "Gross Weight", '
                    f'"Pricepoint" AS "Pricepoint_SKU", "Base Unit of Measure" AS "Unit_of_Measure", '
                    f'"Dial Color", "Case _Frame Size" '
                    f'FROM dbo."Newtrends International Corp_$Item" WITH (NOLOCK) '
                    f'WHERE "No_" IN ({placeholders})')
        items_df = pd.read_sql(item_qry, conn, params=item_list)
        
        merged_df = pd.merge(items_df, prices_df, on="Item No_")

        # 3. Filename Formatting (MySQL Lookups)
        vendor_code, dept_sub, class_sub = "000000", "000000", "000000"
        mysql_conn = get_mysql_conn()
        
        if mysql_conn:
            vendor_map = {"NIC": "NEWTRENDS INTERNATIONAL CORP.", "ATC": "ABOUT TIME CORP.", "TPC": "TIME PLUS CORP."}
            full_vendor = vendor_map.get(company_selection, "")
            
            # Fetch Vendor Code
            v_df = pd.read_sql("SELECT vendor_code FROM vendors WHERE vendor_name = %s LIMIT 1", mysql_conn, params=[full_vendor])
            if not v_df.empty: vendor_code = str(v_df['vendor_code'].iloc[0])

            # Fetch Hierarchy codes for the first item
            first_brand = merged_df['Brand'].iloc[0] if not merged_df.empty else ""
            h_qry = """
                SELECT b.dept_code, b.sub_dept_code, b.class_code, s.subclass_code 
                FROM brands b LEFT JOIN sub_classes s ON b.product_group = s.product_group 
                WHERE b.product_group = %s LIMIT 1
            """
            h_df = pd.read_sql(h_qry, mysql_conn, params=[first_brand])
            if not h_df.empty:
                dept_sub = f"{h_df['dept_code'].iloc[0]}{h_df['sub_dept_code'].iloc[0]}"
                class_sub = f"{h_df['class_code'].iloc[0]}{h_df['subclass_code'].iloc[0]}"
            mysql_conn.close()

        # Build Filename: SC[VendorCode]_[DeptSub]_[ClassSub]_[DateStamp].xlsx
        time_stamp = datetime.now().strftime('%m%d%H%M')
        filename = f"SC{vendor_code}_{dept_sub}_{class_sub}_{time_stamp}.xlsx"

        # 4. Apply Logic (Existing Features)
        merged_df['DESCRIPTION'] = (
            merged_df['Brand'].fillna('') + " " + 
            merged_df['Description'].fillna('') + " " + 
            merged_df['Dial Color'].fillna('') + " " + 
            merged_df['Case _Frame Size'].fillna('') + " " + 
            merged_df['Style_Stockcode'].fillna('')
        ).str.replace(r'[^a-zA-Z0-9\s]', '', regex=True).str[:50]
        
        merged_df['COLOR'] = merged_df['Dial Color'].fillna('')
        merged_df['SIZES'] = merged_df['Case _Frame Size'].fillna('')
        merged_df['SRP'] = merged_df['SRP'].map('{:,.2f}'.format)
        
        # Delivery Date Logic
        now = datetime.now()
        month = now.month + 1
        year = now.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(now.day, calendar.monthrange(year, month)[1])
        merged_df['EXP_DEL_MONTH'] = datetime(year, month, day).strftime('%m/%d/%Y')

        # Weights and Dimensions
        merged_df['SOURCE_MARKED'] = ""; merged_df['REMARKS'] = ""; merged_df['ONLINE ITEMS'] = "NO"
        merged_df['PACKAGE WEIGHT IN KG'] = merged_df['Gross Weight']
        merged_df['PRODUCT WEIGHT IN KG'] = merged_df['Net Weight']
        for dim_col in ['PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 
                        'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM']:
            merged_df[dim_col] = "-"

        final_cols = [
            'DESCRIPTION', 'COLOR', 'SIZES', 'Style_Stockcode', 'SOURCE_MARKED', 
            'SRP', 'Unit_of_Measure', 'EXP_DEL_MONTH', 'REMARKS', 'Pricepoint_SKU', 
            'IMAGES', 'ONLINE ITEMS', 'PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 
            'PACKAGE HEIGHT IN CM', 'PACKAGE WEIGHT IN KG', 'PRODUCT LENGTH IN CM', 
            'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM', 'PRODUCT WEIGHT IN KG'
        ]
        merged_df['IMAGES'] = ""
        
        progress_data["total"] = len(merged_df)
        found_count = 0
        output = io.BytesIO()
        BOX_SIZE_PX, ROW_HEIGHT_PT, COL_WIDTH_PX = 180, 150, 215

        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            merged_df[final_cols].to_excel(writer, sheet_name='Template', index=False, startrow=1)
            workbook, worksheet = writer.book, writer.sheets['Template']
            header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
            
            for col_num, value in enumerate(final_cols):
                worksheet.write(1, col_num, value, header_fmt)
            
            img_col_idx = final_cols.index('IMAGES')
            worksheet.set_column(img_col_idx, img_col_idx, 30)

            for i, item_no in enumerate(merged_df['Item No_']):
                worksheet.set_row(i + 2, ROW_HEIGHT_PT)
                progress_data["current"] = i + 1
                progress_data["status"] = f"Searching for: {item_no}"
                
                img_path = find_image_recursive(NETWORK_IMAGE_PATH, item_no)
                if img_path:
                    try:
                        with Image.open(img_path) as img:
                            w, h = img.size
                        scale = min(BOX_SIZE_PX/w, BOX_SIZE_PX/h)
                        scaled_w, scaled_h = w * scale, h * scale
                        worksheet.insert_image(i + 2, img_col_idx, img_path, {
                            'x_scale': scale, 'y_scale': scale,
                            'x_offset': (COL_WIDTH_PX - scaled_w) / 2, 
                            'y_offset': ((ROW_HEIGHT_PT / 0.75) - scaled_h) / 2,
                            'object_position': 1
                        })
                        found_count += 1
                    except: pass

        output.seek(0)
        # FIX: Explicitly set mimetype to prevent ".jpg" conversion
        excel_mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response = make_response(send_file(output, download_name=filename, as_attachment=True, mimetype=excel_mimetype))
        
        response.headers['X-Total-Items'] = str(progress_data["total"])
        response.headers['X-Images-Found'] = str(found_count)
        response.headers['X-Filename'] = filename
        response.headers['Access-Control-Expose-Headers'] = 'X-Total-Items, X-Images-Found, X-Filename'
        
        return response

    except Exception as e:
        return f"Error: {str(e)}\n\n{traceback.format_exc()}", 500
    finally:
        if conn: conn.close()
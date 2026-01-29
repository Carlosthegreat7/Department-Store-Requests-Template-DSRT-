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
from flask import send_file, make_response, jsonify

# Setup Logging
logger = logging.getLogger(__name__)

def process_atcrep_template(chain_selection, company_selection, pc_memo, sales_code, SQLconnect, get_mysql_conn, build_image_cache, find_image_in_cache, NETWORK_IMAGE_PATH, progress_data):
    # 1. Database and Prefix Setup
    db_name = 'ATCREP'
    table_prefix = 'About Time Corporation' if company_selection == 'ATC' else 'Transcend Prime Inc'
    
    conn = None
    try:
        # --- 2. NAVISION DATA RETRIEVAL ---
        conn, cursor, _ = SQLconnect(db_name, "DSRT")
        if conn is None:
            return jsonify({"error": f"Database Connection to {db_name} Failed"}), 500

        # Fetch Prices
        # Updated SQL Query with built-in deduplication
        price_qry = (
            f'SELECT "Item No_", "SRP" FROM ('
            f'  SELECT "Item No_", "Unit Price" AS "SRP", '
            f'  ROW_NUMBER() OVER (PARTITION BY "Item No_" ORDER BY "Unit Price" DESC) as RowNum '
            f'  FROM dbo."{table_prefix}$Sales Price" WITH (NOLOCK) '
            f'  WHERE "Sales Code"=? AND "PC Memo No"=?'
            f') t WHERE RowNum = 1'
        )

        prices_df = pd.read_sql(price_qry, conn, params=[sales_code, pc_memo])

        if prices_df.empty:
            return jsonify({"error": f"No records found in {db_name} for the provided codes."}), 404

        item_list = prices_df['Item No_'].tolist()
        placeholders = ', '.join(['?'] * len(item_list))

        # 3. Fetch Base Item Data
        base_qry = (f'SELECT "No_" AS "Item No_", "Description", "Product Group Code" AS "Brand", '
                    f'"Vendor Item No_" AS "Style_Stockcode", "Base Unit of Measure" AS "Unit_of_Measure" '
                    f'FROM dbo."{table_prefix}$Item" WITH (NOLOCK) WHERE "No_" IN ({placeholders})')
        items_df = pd.read_sql(base_qry, conn, params=item_list)

        # 4. Fetch and Pivot Attributes (Gender, Color, Size, etc.)
        attr_qry = f'''
            SELECT a."No_", b."Name" as "Attribute", c."Value" 
            FROM dbo."{table_prefix}$Item Attribute Value Mapping" a WITH (NOLOCK)
            LEFT JOIN dbo."{table_prefix}$Item Attribute" b ON a."Item Attribute ID" = b."ID"
            LEFT JOIN dbo."{table_prefix}$Item Attribute Value" c ON a."Item Attribute ID" = c."Attribute ID" 
                 AND a."Item Attribute Value ID" = c."ID"
            WHERE a."Table ID" = 27 AND a."No_" IN ({placeholders})
        '''
        attr_df = pd.read_sql(attr_qry, conn, params=item_list)

        if not attr_df.empty:
            pivoted = attr_df.pivot(index='No_', columns='Attribute', values='Value').reset_index()
            # Rename pivoted columns to align with NIC script logic for Rustans/SM
            rename_map = {
                'Pricepoint': 'Pricepoint_SKU',
                'Dial Color': 'Dial Color',
                'Case _Frame Size': 'Case _Frame Size',
                'Gender': 'Gender'
            }
            pivoted = pivoted.rename(columns=rename_map)
            items_df = pd.merge(items_df, pivoted, how='left', left_on='Item No_', right_on='No_')

        # Ensure all columns exist so formatting logic doesnt crash
        for col in ['Net Weight', 'Gross Weight', 'Pricepoint_SKU', 'Dial Color', 'Case _Frame Size', 'Gender']:
            if col not in items_df.columns: items_df[col] = ""

        # 1. Merge the data
        merged_df = pd.merge(items_df, prices_df, on="Item No_")

        # 2. Force 'SRP' to be a number
        merged_df['SRP'] = pd.to_numeric(merged_df['SRP'], errors='coerce').fillna(0)

        # 3. Sort so that the HIGHEST price is at the top for each Style
        merged_df = merged_df.sort_values(by=['Style_Stockcode', 'SRP'], ascending=[True, False])

        # 4. Remove the duplicates, keeping only the first (highest price) row
        merged_df = merged_df.drop_duplicates(subset=['Style_Stockcode'], keep='first')

        # --- 5. DYNAMIC VENDOR & BRAND LOOKUP (MYSQL) ---
        mysql_conn = get_mysql_conn()
        vendor_code, brand_meta_map = "000000", {}
        if mysql_conn:
            try:
                v_cursor = mysql_conn.cursor()
                v_cursor.execute("SELECT vendor_code FROM vendor_chain_mappings WHERE chain_name = %s AND company_selection = %s", (chain_selection, company_selection))
                v_res = v_cursor.fetchone()
                if v_res: vendor_code = str(v_res[0])

                unique_brands = merged_df['Brand'].unique().tolist()
                if unique_brands:
                    placeholders_sql = ', '.join(['%s'] * len(unique_brands))
                    h_df = pd.read_sql(f"SELECT b.product_group as Brand, b.dept_code, b.sub_dept_code, b.class_code, s.subclass_code FROM brands b LEFT JOIN sub_classes s ON b.product_group = s.product_group WHERE b.product_group IN ({placeholders_sql})", mysql_conn, params=unique_brands)
                    for _, row in h_df.iterrows():
                        brand_meta_map[row['Brand']] = {'dept_sub': f"{row['dept_code']}{row['sub_dept_code']}", 'class_sub': f"{row['class_code']}{row['subclass_code']}"}
            finally:
                mysql_conn.close()

        # --- 6. FORMATTING LOGIC (SM vs RUSTANS) ---
        if chain_selection == "RUSTANS":
            merged_df['RCC SKU'] = ""; merged_df['IMAGE'] = ""
            merged_df['VENDOR ITEM CODE'] = merged_df['Item No_']
            combined_desc_str = (merged_df['Description'].fillna('') + " " + merged_df['Dial Color'].fillna('') + " " + merged_df['Style_Stockcode'].fillna('') + " " + merged_df['Brand'].fillna('')).str.strip()
            merged_df['PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)'] = combined_desc_str.str[:30]
            merged_df['PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)'] = merged_df['Description'].fillna('').str[:10]
            merged_df['PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)'] = combined_desc_str.str[:50]
            merged_df['VENDOR CODE'] = vendor_code
            merged_df['BRAND CODE'] = ""; merged_df['RETAIL PRICE'] = merged_df['SRP'].fillna(0).apply(lambda x: '{:.2f}'.format(x)) 
            merged_df['COLOR'] = merged_df['Dial Color'].fillna(''); merged_df['SIZE'] = merged_df['Case _Frame Size'].fillna(''); merged_df['Gender'] = merged_df['Gender'].fillna('')
            for col in ['DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'SIZE RUN', 'SET / PC', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 'MATERIAL', 'LINK TO HI-RES IMAGE']: merged_df[col] = ""
            final_cols = ['RCC SKU', 'IMAGE', 'VENDOR ITEM CODE', 'PRODUCT MEDIUM DESCRIPTION (CHAR. LIMIT = 30)', 'PRODUCT SHORT DESCRIPTION (CHAR. LIMIT = 10)', 'PRODUCT LONG DESCRIPTION (CHAR. LIMIT = 50)', 'VENDOR CODE', 'BRAND CODE', 'RETAIL PRICE', 'DEPARTMENT', 'SUBDEPARTMENT', 'CLASS', 'SUB CLASS', 'MERCHANDISER', 'BUYER', 'SEASON CODE', 'THEME', 'COLLECTION', 'COLOR', 'SIZE RUN', 'SIZE', 'SET / PC', 'MAKATI', 'SHANG', 'ATC', 'GW', 'CEBU', 'SOLENAD', 'E-COMM (FOR PO)', 'TOTAL', 'TOTAL RETAIL VALUE', 'SIZE SPECIFICATIONS', 'PRODUCT & CARE DETAILS', 'MATERIAL', 'LINK TO HI-RES IMAGE', 'Gender']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGE', "Rustans Template", 14, 15
        else:
            merged_df['DESCRIPTION'] = (merged_df['Brand'].fillna('') + " " + merged_df['Description'].fillna('') + " " + merged_df['Dial Color'].fillna('') + " " + merged_df['Case _Frame Size'].fillna('') + " " + merged_df['Style_Stockcode'].fillna('')).str.replace(r'[^a-zA-Z0-9\s]', '', regex=True).str[:50]
            merged_df['COLOR'] = merged_df['Dial Color'].fillna(''); merged_df['SIZES'] = merged_df['Case _Frame Size'].fillna(''); merged_df['SRP_FMT'] = merged_df['SRP'].fillna(0).map('{:,.2f}'.format)
            now = datetime.now(); month, year = (now.month % 12) + 1, now.year + (1 if now.month == 12 else 0); day = min(now.day, calendar.monthrange(year, month)[1])
            merged_df['EXP_DEL_MONTH'] = datetime(year, month, day).strftime('%m/%d/%Y')
            merged_df['SOURCE_MARKED'] = ""; merged_df['REMARKS'] = ""; merged_df['ONLINE ITEMS'] = "NO"
            merged_df['PACKAGE WEIGHT IN KG'] = merged_df['Gross Weight']; merged_df['PRODUCT WEIGHT IN KG'] = merged_df['Net Weight']
            for dim_col in ['PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM']: merged_df[dim_col] = "-"
            merged_df['IMAGES'] = ""
            final_cols = ['DESCRIPTION', 'COLOR', 'SIZES', 'Style_Stockcode', 'SOURCE_MARKED', 'SRP_FMT', 'Unit_of_Measure', 'EXP_DEL_MONTH', 'REMARKS', 'Pricepoint_SKU', 'IMAGES', 'ONLINE ITEMS', 'PACKAGE LENGTH IN CM', 'PACKAGE WIDTH IN CM', 'PACKAGE HEIGHT IN CM', 'PACKAGE WEIGHT IN KG', 'PRODUCT LENGTH IN CM', 'PRODUCT WIDTH IN CM', 'PRODUCT HEIGHT IN CM', 'PRODUCT WEIGHT IN KG']
            img_col_name, sheet_name_val, header_row_idx, data_start_row = 'IMAGES', "Template", 0, 1

        # --- 7. EXCEL AND IMAGE PROCESSING ---
        progress_data.update({"current": 0, "total": 1, "status": "Indexing Images..."})
        image_cache = build_image_cache(NETWORK_IMAGE_PATH)
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
                    with pd.ExcelWriter(excel_output, engine='xlsxwriter') as writer:
                        bucket_df[final_cols].to_excel(writer, sheet_name=sheet_name_val, index=False, startrow=data_start_row, header=False)
                        workbook, worksheet = writer.book, writer.sheets[sheet_name_val]
                        if chain_selection == "RUSTANS":
                            header_bold = workbook.add_format({'bold': True, 'font_size': 11})
                            worksheet.write('A1', 'Rustan Commercial Corporation', workbook.add_format({'bold': True, 'font_size': 14}))
                            worksheet.write('A7', 'Company:', header_bold); worksheet.write('B7', company_selection)
                            worksheet.write('A8', 'Brand:', header_bold); worksheet.write('B8', brand_name)
                        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
                        for col_num, value in enumerate(final_cols): worksheet.write(header_row_idx, col_num, value, header_fmt)
                        img_col_idx = final_cols.index(img_col_name); worksheet.set_column(img_col_idx, img_col_idx, 35)
                        ROW_HEIGHT_PT = 180
                        for i, item_no in enumerate(bucket_df['Item No_']):
                            processed_count += 1
                            progress_data.update({"current": processed_count, "status": f"Processing {brand_name}: {item_no}"})
                            worksheet.set_row(i + data_start_row, ROW_HEIGHT_PT) 
                            img_path = find_image_in_cache(image_cache, item_no)
                            if img_path:
                                try:
                                    with Image.open(img_path) as img:
                                        img_resized = img.resize((240, int(ROW_HEIGHT_PT / 0.75)), Image.Resampling.LANCZOS)
                                        img_byte_arr = io.BytesIO(); img_resized.save(img_byte_arr, format='PNG'); img_byte_arr.seek(0)
                                    worksheet.insert_image(i + data_start_row, img_col_idx, f"{item_no}.png", {'image_data': img_byte_arr, 'object_position': 1})
                                    total_found_images += 1
                                except: worksheet.write(i + data_start_row, img_col_idx, "CORRUPT IMAGE")
                            else: worksheet.write(i + data_start_row, img_col_idx, "IMAGE NOT FOUND")
                    excel_output.seek(0); zip_file.writestr(filename, excel_output.read())
                except Exception as e: logger.error(f"Brand bucket failed: {e}")

        zip_output.seek(0)
        zip_filename = f"RST_{vendor_code}_{time_stamp}.zip" if chain_selection == "RUSTANS" else f"SC_{vendor_code}_{time_stamp}.zip"
        response = make_response(send_file(zip_output, mimetype='application/zip', as_attachment=True, download_name=zip_filename))
        response.headers.update({'X-Total-Items': str(progress_data["total"]), 'X-Images-Found': str(total_found_images), 'X-Filename': zip_filename, 'Access-Control-Expose-Headers': 'X-Total-Items, X-Images-Found, X-Filename'})
        return response

    except pyodbc.Error as db_err:
        # catches sql errrs
        sql_state = db_err.args[0]
        logger.error(f"SQL Error [{sql_state}]: {str(db_err)}")
        return jsonify({"error": f"Database Query Failed: {str(db_err)}"}), 500

    except Exception as e:
        # catch python errors
        stack_trace = traceback.format_exc()
        logger.error(f"Global ATC Failure: {stack_trace}")
        return jsonify({"error": "An unexpected error occurred during extraction. Check logs."}), 500

    finally:
        if conn:
            conn.close() 
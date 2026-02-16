import pyodbc
import pandas as pd
import sys

def get_connection(dbname):
    all_drivers = [d for d in pyodbc.drivers() if 'SQL Server' in d]
    driver = next((d for d in all_drivers if "17" in d or "18" in d), all_drivers[0])
    conn_str = f'DRIVER={{{driver}}};SERVER=MGSVR14.mgroup.local;DATABASE={dbname};Trusted_Connection=yes;APP=DSRT;TrustServerCertificate=yes;'
    try:
        return pyodbc.connect(conn_str)
    except Exception as e:
        print(f"Failed to connect to {dbname}: {e}")
        sys.exit()

# --- COLUMN DEFINITIONS ---

# Columns that exist in BOTH NIC and ATC/TPC
base_cols = (
    '"No_" as "Item No_", "No_ 2", "Description", "Description 2", "Base Unit of Measure", '
    '"Inventory Posting Group", "Item Disc_ Group", "Allow Invoice Disc_", "Costing Method", '
    '"Vendor No_", "Vendor Item No_", "Gen_ Prod_ Posting Group", "VAT Prod_ Posting Group", '
    '"Reserve", "Global Dimension 2 Code", "Sales Unit of Measure", "Purch_ Unit of Measure", '
    '"WHT Product Posting Group", "Item Category Code", "Product Group Code", "Replenishment System", '
    '"Reordering Policy", "Packaging", "Last Date Modified", "Reorder Point", "Blocked", "NIC Barcode"'
)

# Columns that ONLY exist in NICREP
nic_extra_cols = (
    ', "Common Item No_", "Category_Style", "Classification_Collection", "Line-Up", "Entry Date", '
    '"RPRO DCS", "Source", "Barcode Type", "Gender", "Case_Frame Material", "Case_Frame Color", '
    '"Case_Frame Shape", "Case _Frame Size", "Dial Type", "Dial Color", "Lens Material", '
    '"Strap type_material", "Strap Color", "Point_Power", "Pricepoint", "Discount Level", '
    '"Indiglo_Engraving", "Technology", "Machine", "Strap", "Crown&stem", "Case Assembly", "Dial", "Battery"'
)

# 1. --- NICREP SECTION ---
connection = get_connection('NICREP')
itemcode_nic = 'EES1L512L0015'
# Combine base + extra for NIC
qry_nic = f'select {base_cols} {nic_extra_cols} from dbo."Newtrends International Corp_$Item" with (NOLOCK) where "No_"=? '
Items = pd.read_sql(qry_nic, connection, params=[itemcode_nic])
print("NIC Items Loaded (with Extended Columns)")

# 2. --- ATCREP SECTION ---
atcconnection = get_connection('ATCREP')

def get_atc_item_data(conn, table_prefix, item_code):
    # Use ONLY base_cols here to avoid the "Invalid Column" error
    qry_item = f'select {base_cols} from dbo."{table_prefix}$Item" with (NOLOCK) where "No_"=? '
    df_item = pd.read_sql(qry_item, conn, params=[item_code])
    
    # Dimensions (Table 27 = Item)
    qry_dim = f'select "No_", "Dimension Code", "Dimension Value Code" from dbo."{table_prefix}$Default Dimension" where "Table ID"=27'
    df_dim = pd.read_sql(qry_dim, conn)
    df_dim_piv = df_dim.pivot(index='No_', columns='Dimension Code', values='Dimension Value Code').reset_index()
    
    # Attributes
    qry_attr = (
        f'select a."No_", b."Name" as "Attribute", c."Value" '
        f'from dbo."{table_prefix}$Item Attribute Value Mapping" a '
        f'left join dbo."{table_prefix}$Item Attribute" b on a."Item Attribute ID"=b."ID" '
        f'left join dbo."{table_prefix}$Item Attribute Value" c on a."Item Attribute ID"=c."Attribute ID" and a."Item Attribute Value ID"=c."ID" '
        f'where a."Table ID"=27'
    )
    df_attr = pd.read_sql(qry_attr, conn)
    df_attr_piv = df_attr.pivot(index='No_', columns='Attribute', values='Value').reset_index()
    
    # Merge
    df_final = pd.merge(df_item, df_dim_piv, how='left', left_on='Item No_', right_on='No_').drop(columns=['No_'], errors='ignore')
    df_final = pd.merge(df_final, df_attr_piv, how='left', left_on='Item No_', right_on='No_').drop(columns=['No_'], errors='ignore')
    return df_final

# Fetching Data
ATCItem = get_atc_item_data(atcconnection, "About Time Corporation", 'MO-NA1271042-00')
TPCItem = get_atc_item_data(atcconnection, "Transcend Prime Inc", 'AAH90002620')

print("ATCPrices and TPCItem Loaded successfully (using Base Columns).")
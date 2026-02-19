import pyodbc
import pandas as pd
import sys

# --- HELPER FUNCTIONS ---

def get_connection(dbname):
    all_drivers = [d for d in pyodbc.drivers() if 'SQL Server' in d]
    driver = next((d for d in all_drivers if "17" in d or "18" in d), all_drivers[0])
    conn_str = f'DRIVER={{{driver}}};SERVER=MGSVR14.mgroup.local;DATABASE={dbname};Trusted_Connection=yes;APP=DSRT;TrustServerCertificate=yes;'
    try:
        return pyodbc.connect(conn_str)
    except Exception as e:
        print(f"Failed to connect to {dbname}: {e}")
        sys.exit()

def get_item_data(conn, table_prefix, item_code, column_list):
    """Fetches Item table data, Dimensions, and Attributes."""
    # 1. Main Item Table
    qry_item = f'select {column_list} from dbo."{table_prefix}$Item" with (NOLOCK) where "No_"=? '
    df_item = pd.read_sql(qry_item, conn, params=[item_code])
    
    # 2. Dimensions (Table 27)
    qry_dim = f'select "No_", "Dimension Code", "Dimension Value Code" from dbo."{table_prefix}$Default Dimension" where "Table ID"=27'
    df_dim = pd.read_sql(qry_dim, conn)
    if not df_dim.empty:
        df_dim_piv = df_dim.pivot(index='No_', columns='Dimension Code', values='Dimension Value Code').reset_index()
        df_item = pd.merge(df_item, df_dim_piv, how='left', left_on='Item No_', right_on='No_').drop(columns=['No_'], errors='ignore')
    
    # 3. Attributes
    qry_attr = (
        f'select a."No_", b."Name" as "Attribute", c."Value" '
        f'from dbo."{table_prefix}$Item Attribute Value Mapping" a '
        f'left join dbo."{table_prefix}$Item Attribute" b on a."Item Attribute ID"=b."ID" '
        f'left join dbo."{table_prefix}$Item Attribute Value" c on a."Item Attribute ID"=c."Attribute ID" and a."Item Attribute Value ID"=c."ID" '
        f'where a."Table ID"=27'
    )
    df_attr = pd.read_sql(qry_attr, conn)
    if not df_attr.empty:
        df_attr_piv = df_attr.pivot(index='No_', columns='Attribute', values='Value').reset_index()
        df_item = pd.merge(df_item, df_attr_piv, how='left', left_on='Item No_', right_on='No_').drop(columns=['No_'], errors='ignore')
    
    return df_item

# --- COLUMN SCHEMAS ---

base_cols = (
    '"No_" as "Item No_", "No_ 2", "Description", "Description 2", "Base Unit of Measure", '
    '"Inventory Posting Group", "Item Disc_ Group", "Allow Invoice Disc_", "Costing Method", '
    '"Vendor No_", "Vendor Item No_", "Gen_ Prod_ Posting Group", "VAT Prod_ Posting Group", '
    '"Reserve", "Global Dimension 2 Code", "Sales Unit of Measure", "Purch_ Unit of Measure", '
    '"WHT Product Posting Group", "Item Category Code", "Product Group Code", "Replenishment System", '
    '"Reordering Policy", "Packaging", "Last Date Modified", "Reorder Point", "Blocked", "NIC Barcode"'
)

nic_extra_cols = (
    ', "Common Item No_", "Category_Style", "Classification_Collection", "Line-Up", "Entry Date", '
    '"RPRO DCS", "Source", "Barcode Type", "Gender", "Case_Frame Material", "Case_Frame Color", '
    '"Case_Frame Shape", "Case _Frame Size", "Dial Type", "Dial Color", "Lens Material", '
    '"Strap type_material", "Strap Color", "Point_Power", "Pricepoint", "Discount Level", '
    '"Indiglo_Engraving", "Technology", "Machine", "Strap", "Crown&stem", "Case Assembly", "Dial", "Battery"'
)

# --- DATA RETRIEVAL ---

# 1. NICREP (Newtrends)
conn_nic = get_connection('NICREP')
NICPrices = pd.read_sql('select "Item No_","Unit Price" as MSRP from dbo."Newtrends International Corp_$Sales Price" where "Sales Code"=\'MSRP\' and "PC Memo No"=\'PRCMMO-0004352\'', conn_nic)
NICItem = get_item_data(conn_nic, "Newtrends International Corp_", 'EES1L512L0015', f'{base_cols} {nic_extra_cols}')

# 2. ATCREP (About Time & Transcend)
conn_atc = get_connection('ATCREP')
ATCPrices = pd.read_sql('select "Item No_","Unit Price" as MSRP from dbo."About Time Corporation$Sales Price" where "Sales Code"=\'MSRP\' and "PC Memo No"=\'PERM-TIMEX-231001\'', conn_atc)
ATCItem = get_item_data(conn_atc, "About Time Corporation", 'MO-NA1271042-00', base_cols)

TPCPrices = pd.read_sql('select "Item No_","Unit Price" as MSRP from dbo."Transcend Prime Inc$Sales Price" where "Sales Code"=\'MSRP\' and "PC Memo No"=\'PERM-SIGNA-251201\'', conn_atc)
TPCItem = get_item_data(conn_atc, "Transcend Prime Inc", 'AAH90002620', base_cols)

# --- FINAL PRINTING ---

datasets = [
    ("NIC (Newtrends)", NICItem, NICPrices),
    ("ATC (About Time)", ATCItem, ATCPrices),
    ("TPC (Transcend Prime)", TPCItem, TPCPrices)
]

for name, item_df, price_df in datasets:
    print(f"\n{'='*50}")
    print(f" DATA FOR: {name} ")
    print(f"{'='*50}")
    
    print("\n[ PRICES ]")
    print(price_df.to_string(index=False) if not price_df.empty else "No price data found.")
    
    print("\n[ ITEM DETAILS ]")
    # Transposing the print makes it much easier to read many columns at once
    print(item_df.T) 
    
    print(f"\nTotal Columns Retrieved for {name}: {len(item_df.columns)}")
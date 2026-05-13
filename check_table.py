import pyodbc
import pandas as pd
import warnings

# Suppress the pandas SQLAlchemy warning for a cleaner terminal output
warnings.filterwarnings('ignore', category=UserWarning)

def view_barcodes_data():
    try:
        # Use the exact same connection logic that works in your app
        drivers = [d for d in pyodbc.drivers() if 'ODBC Driver' in d and 'SQL Server' in d]
        driver  = next((d for v in ['18', '17', '13'] for d in drivers if v in d), drivers[0] if drivers else None)
        
        conn_str = f"DRIVER={{{driver}}};SERVER=MGSVR14;DATABASE=Barcodes;Trusted_Connection=yes;"
        if "18" in driver:
            conn_str += "TrustServerCertificate=yes;"
            
        conn = pyodbc.connect(conn_str, timeout=5)
        
        # Query the top 50 most recent rows
        query = "SELECT TOP 50 ITEM_CODE, BARCODE, [DESC], VENDOR, BRAND_CODE, SKU FROM dbo.barcodes ORDER BY DATEADDED DESC"
        
        df = pd.read_sql(query, conn)
        
        if df.empty:
            print("The dbo.barcodes table is currently EMPTY. No records found.")
        else:
            print(f"--- Top {len(df)} Latest Rows in dbo.barcodes ---")
            print(df.to_string(index=False))
            
    except Exception as e:
        print(f"Error reading rows: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    view_barcodes_data()
import pyodbc

# Exact name from your Drivers tab
DRIVER = '{ODBC Driver 18 for SQL Server}' 
SERVER = 'MGSVR14.mgroup.local'
DATABASE = 'ATCREP'

def test_connection():
    # CRITICAL: Added 'TrustServerCertificate=yes' for Driver 18 compatibility
    conn_str = (
        f'DRIVER={DRIVER};'
        f'SERVER={SERVER};'
        f'DATABASE={DATABASE};'
        f'Trusted_Connection=yes;'
        f'TrustServerCertificate=yes;' 
    )
    
    try:
        print(f"Testing Connection with: {DRIVER}")
        conn = pyodbc.connect(conn_str)
        print("✅ SUCCESS: Connection established to ATCREP!")
        conn.close()
    except Exception as e:
        print(f"❌ FAILED: {str(e)}")

if __name__ == "__main__":
    test_connection()
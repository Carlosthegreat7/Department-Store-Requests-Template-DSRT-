import pyodbc

# Test script to check user connection to ATCREP
test_string = (
    f'DRIVER={{ODBC Driver 17 for SQL Server}};'
    f'SERVER=MGSVR14.mgroup.local;'
    f'DATABASE=ATCREP;'
    f'Trusted_Connection=yes;'
)

try:
    conn = pyodbc.connect(test_string)
    print("Success! You have permission to read ATCREP.")
    conn.close()
except Exception as e:
    print(f"Permission Denied or Connection Error: {e}")
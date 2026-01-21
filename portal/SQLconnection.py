import pyodbc
#How to import:
# from portal.SQLconnection import SQLconnect

def get_db_info(dbname):
    # Connection string to connect to the sysDev database
    # Updated to ODBC Driver 18 + TrustServerCertificate for 2026 compatibility
    sysdev_connection_string = (
        'DRIVER={ODBC Driver 18 for SQL Server};'
        'SERVER=MGSVR14.mgroup.local;'
        'DATABASE=MIS_SysDev;'
        'UID=nicportal;'
        'PWD=n1cp0rtal;'
        'READONLY=True;'
        'TrustServerCertificate=yes;'
    )

    sysdev_connection = None
    try:
        # Connect to the sysDev database
        sysdev_connection = pyodbc.connect(sysdev_connection_string)
        cursor = sysdev_connection.cursor()

        # Query to get the connection info for the specified database
        cursor.execute("SELECT [DB Name], [TD Prefix], Server, UID, PWD, [Trusted Connection] FROM DB_Info WHERE [Code] = ?", dbname)

        # Fetch the first row (assuming database names are unique)
        db_info = cursor.fetchone()

        return db_info

    except Exception as e:
        # print("Error retrieving database info: ", e)
        return None

    finally:
        if sysdev_connection:
            sysdev_connection.close()

def SQLconnect(dbname, app_name):
    # Get the database info
    # print("app_name: ",app_name)
    db_info = get_db_info(dbname)

    if db_info is None:
        # print(f"No connection info found for database: {dbname}")
        return None, None, None  # Return None for all if no info found

    database, TD_Prefix, server, UID, PWD, trusted_connection = db_info

    # Construct the connection string using ODBC Driver 18
    # Added TrustServerCertificate=yes to handle Driver 18's stricter encryption defaults
    if trusted_connection.lower() == 'yes':
        connection_string = f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};DATABASE={database};Trusted_Connection=yes;APP={app_name};TrustServerCertificate=yes;'
    else:
        connection_string = f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};DATABASE={database};UID={UID};PWD={PWD};APP={app_name};TrustServerCertificate=yes;'

    try:
        # Connect to the target database
        connection = pyodbc.connect(connection_string)
        # print(f"Connected to {database} on {server} successfully!")

        # Create a cursor object
        cursor = connection.cursor()

        # Return the connection object, cursor, and TD_Prefix for further use
        return connection, cursor, TD_Prefix

    except Exception as e:
        print("Error connecting to target database: ", e)
        return None, None, None  # Return None for all if connection fails

#How to use:
#NIC_NAV_connect, NIC_NAV_cursor, NIC_NAV_prefix = SQLconnect(app.config['NIC_NAV_connect'], app_name)
import pyodbc
import logging

logger = logging.getLogger(__name__)

def get_installed_driver():
    """Dynamically detects the best available ODBC driver on the server."""
    drivers = [d for d in pyodbc.drivers() if 'ODBC Driver' in d and 'SQL Server' in d]
    if not drivers:
        # Fallback to older legacy driver if no modern drivers are found
        return '{SQL Server}'
    
    # Prioritize Driver 18, then 17, then others
    for version in ['18', '17', '13']:
        for d in drivers:
            if version in d:
                return f"{{{d}}}"
    return f"{{{drivers[0]}}}"

def get_db_info(dbname):
    driver = get_installed_driver()
    # Driver 18 requires TrustServerCertificate=yes; Driver 17 ignores it but it doesn't hurt.
    trust_cert = ";TrustServerCertificate=yes" if "18" in driver else ""
    
    sysdev_connection_string = (
        f'DRIVER={driver};'
        'SERVER=MGSVR14.mgroup.local;'
        'DATABASE=MIS_SysDev;'
        'UID=nicportal;'
        'PWD=n1cp0rtal;'
        f'READONLY=True{trust_cert};'
    )

    sysdev_connection = None
    try:
        sysdev_connection = pyodbc.connect(sysdev_connection_string)
        cursor = sysdev_connection.cursor()
        cursor.execute("SELECT [DB Name], [TD Prefix], Server, UID, PWD, [Trusted Connection] FROM DB_Info WHERE [Code] = ?", dbname)
        return cursor.fetchone()
    except Exception as e:
        logger.error(f"Error retrieving database info for {dbname}: {e}")
        return None
    finally:
        if sysdev_connection:
            sysdev_connection.close()

def SQLconnect(dbname, app_name):
    db_info = get_db_info(dbname)

    if db_info is None:
        return None, None, None

    database, TD_Prefix, server, UID, PWD, trusted_connection = db_info
    driver = get_installed_driver()
    trust_cert = ";TrustServerCertificate=yes" if "18" in driver else ""

    if trusted_connection.lower() == 'yes':
        connection_string = f'DRIVER={driver};SERVER={server};DATABASE={database};Trusted_Connection=yes;APP={app_name}{trust_cert};'
    else:
        connection_string = f'DRIVER={driver};SERVER={server};DATABASE={database};UID={UID};PWD={PWD};APP={app_name}{trust_cert};'

    try:
        connection = pyodbc.connect(connection_string)
        cursor = connection.cursor()
        # Ensure TD_Prefix is handled correctly (returning None if missing in DB_Info)
        return connection, cursor, TD_Prefix
    except Exception as e:
        print(f"Error connecting to target database {database}: {e}")
        return None, None, None
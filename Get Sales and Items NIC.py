
import pyodbc
import pandas as pd
from datetime import datetime

try:
    connection_string = f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER=MGSVR14.mgroup.local;DATABASE=NICREP;Trusted_Connection=yes;TrustServerCertificate=yes;APP=DSRT;'
    connection = pyodbc.connect(connection_string)
    print(f"Connected to NICREP on MGSVR14.mgroup.local successfully!")
    cursor = connection.cursor()

except Exception as e:
    print("Error connecting to target database: ", e)
    #go back to the main page

LCSALESCODE = 'MSRP'
LCPCM       = 'PRCMMO-0004352'


qry = 'select "Item No_","Unit Price" as MSRP ' \
       'from dbo."Newtrends International Corp_$Sales Price" with (NOLOCK) ' \
       'where "Sales Code"=? and "PC Memo No"=? '
Prices = pd.read_sql(qry, connection, params=[LCSALESCODE, LCPCM])
# Prices is a pandas dataframe that contains the data for the stated pcm# from the MS SQL database
print("Prices")
print(Prices)


itemcode = 'EES1L512L0015'
qry = 'select "No_" as "Item No_", "No_ 2", "Common Item No_", "Description", "Description 2", "Base Unit of Measure", "Inventory Posting Group", ' \
			'"Item Disc_ Group", "Allow Invoice Disc_", "Costing Method", "Vendor No_", "Vendor Item No_", "Gen_ Prod_ Posting Group", ' \
			'"VAT Prod_ Posting Group", "Reserve", "Global Dimension 2 Code", "Sales Unit of Measure", "Purch_ Unit of Measure", "WHT Product Posting Group", ' \
			'"Item Category Code", "Product Group Code", "Replenishment System", "Reordering Policy", "Category_Style", "Classification_Collection", ' \
			'"Line-Up", "Entry Date", "RPRO DCS", "Source", "Barcode Type", "Gender" , "Case_Frame Material", "Case_Frame Color", "Case_Frame Shape", "Case _Frame Size", ' \
			'"Dial Type", "Dial Color", "Lens Material", "Strap type_material", "Strap Color", "Point_Power", "Pricepoint", "Discount Level", "Indiglo_Engraving", "Technology", ' \
			'"Machine","Strap","Crown&stem","Case Assembly","Dial","Battery","Packaging", "Last Date Modified","Reorder Point", "Blocked", "NIC Barcode"' \
			'from dbo."Newtrends International Corp_$Item" with (NOLOCK) where "No_"=? ' \
             'order by "Item Category Code", "Product Group Code","No_" '
Items = pd.read_sql(qry, connection, params=[itemcode])
# Items is a pandas dataframe that contains the data for the stated item from the MS SQL database
print("Items")
print(Items)



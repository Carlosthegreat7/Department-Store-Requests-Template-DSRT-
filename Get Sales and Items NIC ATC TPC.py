
import pyodbc
import pandas as pd
from datetime import datetime

try:
    connection_string = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER=MGSVR14.mgroup.local;DATABASE=NICREP;Trusted_Connection=yes;APP=DSRT;'
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




try:
    atcconnection_string = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER=MGSVR14.mgroup.local;DATABASE=ATCREP;Trusted_Connection=yes;APP=DSRT;'
    atcconnection = pyodbc.connect(atcconnection_string)
    print(f"Connected to ATCREP on MGSVR14.mgroup.local successfully!")
    atccursor = atcconnection.cursor()

except Exception as e:
    print("Error connecting to target database: ", e)
    #go back to the main page

LCSALESCODE = 'MSRP'
LCPCM       = 'PERM-TIMEX-231001'


qry = 'select "Item No_","Unit Price" as MSRP ' \
       'from dbo."About Time Corporation$Sales Price" with (NOLOCK) ' \
       'where "Sales Code"=? and "PC Memo No"=? '
ATCPrices = pd.read_sql(qry, atcconnection, params=[LCSALESCODE, LCPCM])
# Prices is a pandas dataframe that contains the data for the stated pcm# from the MS SQL database
print("ATCPrices")
print(ATCPrices)

LCPCM       = 'PERM-SIGNA-251201'
qry = 'select "Item No_","Unit Price" as MSRP ' \
       'from dbo."Transcend Prime Inc$Sales Price" with (NOLOCK) ' \
       'where "Sales Code"=? and "PC Memo No"=? '
TPCPrices = pd.read_sql(qry, atcconnection, params=[LCSALESCODE, LCPCM])
# Prices is a pandas dataframe that contains the data for the stated pcm# from the MS SQL database
print("TPCPrices")
print(TPCPrices)



itemcode = 'MO-NA1271042-00'
qry1 = 'select "No_" as "Item No_", "No_ 2", "Description", "Description 2", "Base Unit of Measure", "Inventory Posting Group", ' \
			'"Item Disc_ Group", "Allow Invoice Disc_", "Costing Method", "Vendor No_", "Vendor Item No_", "Gen_ Prod_ Posting Group", '\
			'"VAT Prod_ Posting Group", "Reserve", "Global Dimension 2 Code", "Sales Unit of Measure", "Purch_ Unit of Measure", "WHT Product Posting Group", '\
			'"Item Category Code", "Product Group Code", "Replenishment System", "Reordering Policy", "Packaging", "Last Date Modified","Reorder Point", "Blocked", "NIC Barcode" ' \
			'from dbo."About Time Corporation$Item" with (NOLOCK) where "No_"=? order by "Item Category Code", "Item Disc_ Group","No_" '
ATCItem = pd.read_sql(qry1, atcconnection, params=[itemcode])

qry1 = 'select "No_", "Dimension Code", "Dimension Value Code" ' \
	'from dbo."About Time Corporation$Default Dimension" with (NOLOCK)  ' \
	'where "Table ID"=27 '

ATCitemdimension	= pd.read_sql(qry1, atcconnection)
ATCdf_pivoted		= ATCitemdimension.pivot(index=['No_'],columns=['Dimension Code'])
ATCdf_pivoted.columns	= ATCdf_pivoted.columns.droplevel(0)
ATCdf_pivoted		= ATCdf_pivoted.reset_index()
ATCItem			= pd.merge(ATCItem, ATCdf_pivoted, how='left', left_on=['Item No_'], right_on=['No_'])
ATCItem			= ATCItem.drop(columns=['No_'])

qry1 = 'select a."No_", b."Name" as "Attribute", c."Value" ' \
	'from dbo."About Time Corporation$Item Attribute Value Mapping" a with (NOLOCK) ' \
	'left join	dbo."About Time Corporation$Item Attribute" b with (NOLOCK) on  a."Item Attribute ID"=b."ID" ' \
	'left join	dbo."About Time Corporation$Item Attribute Value" c with (NOLOCK) on	a."Item Attribute ID"=c."Attribute ID" and a."Item Attribute Value ID"=c."ID" ' \
	'where a."Table ID"=27 '
ATCitemattrib			= pd.read_sql(qry1, atcconnection)
ATCitemattrib_pivoted		= ATCitemattrib.pivot(index=['No_'],columns=['Attribute'])
ATCitemattrib_pivoted.columns	= ATCitemattrib_pivoted.columns.droplevel(0)
ATCitemattrib_pivoted		= ATCitemattrib_pivoted.reset_index()
ATCItem				= pd.merge(ATCItem, ATCitemattrib_pivoted, how='left', left_on=['Item No_'], right_on=['No_'])
ATCItem				= ATCItem.drop(columns=['No_'])
print("ATCItem")
print(ATCItem)

itemcode = 'AAH90002620'
qry1 = 'select "No_" as "Item No_", "No_ 2", "Description", "Description 2", "Base Unit of Measure", "Inventory Posting Group", ' \
			'"Item Disc_ Group", "Allow Invoice Disc_", "Costing Method", "Vendor No_", "Vendor Item No_", "Gen_ Prod_ Posting Group", '\
			'"VAT Prod_ Posting Group", "Reserve", "Global Dimension 2 Code", "Sales Unit of Measure", "Purch_ Unit of Measure", "WHT Product Posting Group", '\
			'"Item Category Code", "Product Group Code", "Replenishment System", "Reordering Policy", "Packaging", "Last Date Modified","Reorder Point", "Blocked", "NIC Barcode" ' \
			f'from dbo."Transcend Prime Inc$Item" with (NOLOCK) where "No_"=? order by "Item Category Code", "Item Disc_ Group","No_" '
TPCItem = pd.read_sql(qry1, atcconnection, params=[itemcode])

qry1 = 'select "No_", "Dimension Code", "Dimension Value Code" ' \
	'from dbo."Transcend Prime Inc$Default Dimension" with (NOLOCK)  ' \
	'where "Table ID"=27 '

TPCitemdimension	= pd.read_sql(qry1, atcconnection)
TPCdf_pivoted		= TPCitemdimension.pivot(index=['No_'],columns=['Dimension Code'])
TPCdf_pivoted.columns	= TPCdf_pivoted.columns.droplevel(0)
TPCdf_pivoted		= TPCdf_pivoted.reset_index()
TPCItem			= pd.merge(TPCItem, TPCdf_pivoted, how='left', left_on=['Item No_'], right_on=['No_'])
TPCItem			= TPCItem.drop(columns=['No_'])


qry1 = 'select a."No_", b."Name" as "Attribute", c."Value" ' \
	'from dbo."Transcend Prime Inc$Item Attribute Value Mapping" a with (NOLOCK) ' \
	'left join	dbo."Transcend Prime Inc$Item Attribute" b with (NOLOCK) on  a."Item Attribute ID"=b."ID" ' \
	'left join	dbo."Transcend Prime Inc$Item Attribute Value" c with (NOLOCK) on	a."Item Attribute ID"=c."Attribute ID" and a."Item Attribute Value ID"=c."ID" ' \
	'where a."Table ID"=27 '
TPCitemattrib			= pd.read_sql(qry1, atcconnection)
TPCitemattrib_pivoted		= TPCitemattrib.pivot(index=['No_'],columns=['Attribute'])
TPCitemattrib_pivoted.columns	= TPCitemattrib_pivoted.columns.droplevel(0)
TPCitemattrib_pivoted		= TPCitemattrib_pivoted.reset_index()
TPCItem				= pd.merge(TPCItem, TPCitemattrib_pivoted, how='left', left_on=['Item No_'], right_on=['No_'])
TPCItem				= TPCItem.drop(columns=['No_'])

print("TPCItem")
print(TPCItem)








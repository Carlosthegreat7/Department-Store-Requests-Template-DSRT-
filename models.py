from extensions import db

class Vendor(db.Model):
    """
    Model for the 'vendors' table.
    Stores vendor codes and their corresponding names.
    """
    __tablename__ = 'vendors'
    # vendor_code is the primary key, typically 6 digits (e.g., 014353)
    vendor_code = db.Column(db.String(10), primary_key=True)
    vendor_name = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f'<Vendor {self.vendor_code}: {self.vendor_name}>'


class Brand(db.Model):
    """
    Model for the 'brands' table (Hierarchy).
    Stores brand details and their placement within the product hierarchy.
    """
    __tablename__ = 'brands'
    # brand_name acts as the unique identifier for hierarchy entries
    brand_name = db.Column(db.String(100), primary_key=True)
    product_group = db.Column(db.String(100), nullable=False)
    # Codes are stored as strings to preserve leading zeros (e.g., '054')
    dept_code = db.Column(db.String(3), nullable=False)
    sub_dept_code = db.Column(db.String(3), nullable=False)
    class_code = db.Column(db.String(3), nullable=False)

    def __repr__(self):
        return f'<Brand {self.brand_name} ({self.product_group})>'


class SubClass(db.Model):
    __tablename__ = 'sub_classes'
    # Setting both as primary_key=True tells SQLAlchemy this is a composite key
    product_group = db.Column(db.String(100), primary_key=True)
    subclass_code = db.Column(db.String(3), primary_key=True)
    subclass_name = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f'<SubClass {self.product_group}-{self.subclass_code}>'
    

#ensure that the database schema matches these models, especially regarding primary keys and data types. ang tagal ko to hinanap 
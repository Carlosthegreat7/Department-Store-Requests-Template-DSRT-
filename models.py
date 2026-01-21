from extensions import db

class Vendor(db.Model):
    """Model for the 'vendors' table for SM."""
    __tablename__ = 'vendors'
    vendor_code = db.Column(db.String(10), primary_key=True)
    vendor_name = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f'<Vendor {self.vendor_code}: {self.vendor_name}>'


class Brand(db.Model):
    """Model for the 'brands' table for SM."""
    __tablename__ = 'brands'
    brand_name = db.Column(db.String(100), primary_key=True)
    product_group = db.Column(db.String(100), nullable=False)
    dept_code = db.Column(db.String(3), nullable=False)
    sub_dept_code = db.Column(db.String(3), nullable=False)
    class_code = db.Column(db.String(3), nullable=False)

    def __repr__(self):
        return f'<Brand {self.brand_name} ({self.product_group})>'


class SubClass(db.Model):
    """Model for the 'sub_classes' table for SM."""
    __tablename__ = 'sub_classes'
    product_group = db.Column(db.String(100), primary_key=True)
    subclass_code = db.Column(db.String(10), primary_key=True)
    subclass_name = db.Column(db.String(100), nullable=False)

    def __repr__(self):
        return f'<SubClass {self.product_group} - {self.subclass_name}>'


# --- NEW RDS MODELS ---

class VendorRDS(db.Model):
    """Model for 'vendors_rds' table."""
    __tablename__ = 'vendors_rds'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    company_name = db.Column(db.String(255), nullable=False)
    vendor_code = db.Column(db.String(20), nullable=False)
    mfg_part_no = db.Column(db.String(20), nullable=False)

    def __repr__(self):
        return f'<VendorRDS {self.company_name}>'


class HierarchyRDS(db.Model):
    """Model for 'hierarchy_rds' table."""
    __tablename__ = 'hierarchy_rds'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    dept = db.Column(db.String(10), nullable=False)
    sdept = db.Column(db.String(10), nullable=False)
    # Using db.Column with name 'class' because it is a reserved word in Python
    class_code = db.Column('class', db.String(10), nullable=False)
    sclass = db.Column(db.String(10), nullable=False)
    sclass_name = db.Column(db.String(100), nullable=False)

    def __repr__(self):
        return f'<HierarchyRDS {self.sclass_name}>'


class PricePointRDS(db.Model):
    """Model for 'price_points_rds' table."""
    __tablename__ = 'price_points_rds'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    price_point_code = db.Column(db.String(10), nullable=False)
    price_point_desc = db.Column(db.String(100), nullable=False)

    def __repr__(self):
        return f'<PricePointRDS {self.price_point_code}>'


class AgeCodeRDS(db.Model):
    """Model for 'age_codes_rds' table."""
    __tablename__ = 'age_codes_rds'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    age_code = db.Column(db.String(10), nullable=False)
    description = db.Column(db.String(100), nullable=False)

    def __repr__(self):
        return f'<AgeCodeRDS {self.age_code}>'
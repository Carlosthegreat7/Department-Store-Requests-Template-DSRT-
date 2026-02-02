from flask import Blueprint, render_template, session, redirect, url_for, flash, request
from portal import loggedin_required
from models import VendorRDS, HierarchyRDS, PricePointRDS, AgeCodeRDS
from extensions import db
import mysql.connector

# Define blueprint
rds_mng_bp = Blueprint('rds_mng', __name__)

def get_mysql_conn():
    try:
        return mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="myproject"
        )
    except Exception:
        return None

@rds_mng_bp.route('/admin/management/rds', methods=['GET'])
@loggedin_required()
def admin_management_rds():
    # Security Check
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))
    
    # Fetch all RDS data from the database
    vendors_rds = VendorRDS.query.order_by(VendorRDS.id.desc()).all()
    hierarchies_rds = HierarchyRDS.query.all()
    price_points_rds = PricePointRDS.query.all()
    age_codes_rds = AgeCodeRDS.query.all()
    
    return render_template('admin_management_rds.html', 
                           vendors=vendors_rds,
                           hierarchies=hierarchies_rds,
                           price_points=price_points_rds,
                           age_codes=age_codes_rds)

@rds_mng_bp.route('/admin/management/rds/add_vendor', methods=['POST'])
@loggedin_required()
def add_vendor_rds():
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    company_name = request.form.get('company_name')
    vendor_code = request.form.get('vendor_code')
    mfg_part_no = request.form.get('mfg_part_no')

    if not company_name or not vendor_code or not mfg_part_no:
        flash("Error: All fields are required")
        return redirect(url_for('rds_mng.admin_management_rds'))

    existing = VendorRDS.query.filter_by(vendor_code=vendor_code).first()
    if existing:
        flash(f"Error: Vendor Code {vendor_code} already exists")
        return redirect(url_for('rds_mng.admin_management_rds'))

    try:
        new_vendor = VendorRDS(
            company_name=company_name,
            vendor_code=vendor_code,
            mfg_part_no=mfg_part_no
        )
        db.session.add(new_vendor)
        db.session.commit()

        # === MYSQL SYNC FOR TRANSACTION FORM (PATCHED FOR RDS) ===
        try:
            conn = get_mysql_conn()
            if conn:
                cursor = conn.cursor()
                sync_qry = (
                    "INSERT INTO vendor_chain_mappings (chain_name, company_selection, vendor_code) "
                    "VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE vendor_code=%s"
                )
                # Chain identifier is now RDS
                cursor.execute(sync_qry, ('RDS', company_name, vendor_code, vendor_code))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"MySQL Sync Warning: {e}")

        flash("RDS Vendor successfully added", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds'))

@rds_mng_bp.route('/admin/management/rds/edit_vendor/<int:id>', methods=['POST'])
@loggedin_required()
def edit_vendor_rds(id):
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    vendor = VendorRDS.query.get_or_404(id)
    
    company_name = request.form.get('company_name')
    vendor_code = request.form.get('vendor_code')
    mfg_part_no = request.form.get('mfg_part_no')

    if not company_name or not vendor_code or not mfg_part_no:
        flash("Error: All fields are required")
        return redirect(url_for('rds_mng.admin_management_rds'))

    # Check for duplicate vendor code if it changed
    if vendor.vendor_code != vendor_code:
        existing = VendorRDS.query.filter_by(vendor_code=vendor_code).first()
        if existing:
            flash(f"Error: Vendor Code {vendor_code} already exists")
            return redirect(url_for('rds_mng.admin_management_rds'))

    try:
        # Store old values for the SQL Update mapping
        old_vendor_code = vendor.vendor_code
        
        # Update SQLAlchemy model
        vendor.company_name = company_name
        vendor.vendor_code = vendor_code
        vendor.mfg_part_no = mfg_part_no
        
        db.session.commit()

        # === MYSQL SYNC FOR TRANSACTION FORM (PATCHED FOR RDS) ===
        try:
            conn = get_mysql_conn()
            if conn:
                cursor = conn.cursor()
                # Update both code and name in the bridge table where chain is RDS
                update_qry = (
                    "UPDATE vendor_chain_mappings "
                    "SET vendor_code=%s, company_selection=%s "
                    "WHERE vendor_code=%s AND chain_name='RDS'"
                )
                cursor.execute(update_qry, (vendor_code, company_name, old_vendor_code))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"MySQL Sync Warning: {e}")

        flash("RDS Vendor successfully updated", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds'))

@rds_mng_bp.route('/admin/management/rds/delete-vendor/<int:id>', methods=['POST'])
@loggedin_required()
def delete_vendor_rds(id):
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    vendor = VendorRDS.query.get_or_404(id)
    vendor_code = vendor.vendor_code
    
    try:
        # sync delete
        try:
            conn = get_mysql_conn()
            if conn:
                cursor = conn.cursor()
                del_qry = "DELETE FROM vendor_chain_mappings WHERE vendor_code=%s AND chain_name='RUSTANS'"
                cursor.execute(del_qry, (vendor_code,))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"MySQL Sync Delete Warning: {e}")

        db.session.delete(vendor)
        db.session.commit()
        flash("RDS Vendor deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds'))

# --- HIERARCHY MANAGEMENT ---

@rds_mng_bp.route('/admin/management/rds/add_hierarchy', methods=['POST'])
@loggedin_required()
def add_hierarchy_rds():
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    dept = request.form.get('dept')
    sdept = request.form.get('sdept')
    class_code = request.form.get('class_code')
    sclass = request.form.get('sclass')
    sclass_name = request.form.get('sclass_name')

    if not all([dept, sdept, class_code, sclass, sclass_name]):
        flash("Error: All fields are required")
        return redirect(url_for('rds_mng.admin_management_rds', _anchor='hierarchy'))

    try:
        new_hierarchy = HierarchyRDS(
            dept=dept,
            sdept=sdept,
            class_code=class_code,
            sclass=sclass,
            sclass_name=sclass_name
        )
        db.session.add(new_hierarchy)
        db.session.commit()
        flash("Hierarchy added successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds', _anchor='hierarchy'))

@rds_mng_bp.route('/admin/management/rds/edit_hierarchy/<int:id>', methods=['POST'])
@loggedin_required()
def edit_hierarchy_rds(id):
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    h = HierarchyRDS.query.get_or_404(id)
    
    dept = request.form.get('dept')
    sdept = request.form.get('sdept')
    class_code = request.form.get('class_code')
    sclass = request.form.get('sclass')
    sclass_name = request.form.get('sclass_name')

    if not all([dept, sdept, class_code, sclass, sclass_name]):
        flash("Error: All fields are required")
        return redirect(url_for('rds_mng.admin_management_rds', _anchor='hierarchy'))

    try:
        h.dept = dept
        h.sdept = sdept
        h.class_code = class_code
        h.sclass = sclass
        h.sclass_name = sclass_name
        
        db.session.commit()
        flash("Hierarchy updated successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds', _anchor='hierarchy'))


@rds_mng_bp.route('/admin/management/rds/delete-hierarchy/<int:id>', methods=['POST'])
@loggedin_required()
def delete_hierarchy_rds(id):
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    h = HierarchyRDS.query.get_or_404(id)
    try:
        db.session.delete(h)
        db.session.commit()
        flash("Hierarchy deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")
    
    return redirect(url_for('rds_mng.admin_management_rds', _anchor='hierarchy'))

# --- PRICE POINT MANAGEMENT ---

@rds_mng_bp.route('/admin/management/rds/add_price_point', methods=['POST'])
@loggedin_required()
def add_price_point_rds():
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    price_point_code = request.form.get('price_point_code')
    price_point_desc = request.form.get('price_point_desc')

    if not price_point_code or not price_point_desc:
        flash("Error: All fields are required")
        return redirect(url_for('rds_mng.admin_management_rds', _anchor='price'))

    existing = PricePointRDS.query.filter_by(price_point_code=price_point_code).first()
    if existing:
        flash(f"Error: Price Point Code {price_point_code} already exists")
        return redirect(url_for('rds_mng.admin_management_rds', _anchor='price'))

    try:
        new_pp = PricePointRDS(
            price_point_code=price_point_code,
            price_point_desc=price_point_desc
        )
        db.session.add(new_pp)
        db.session.commit()
        flash("Price Point added successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds', _anchor='price'))

@rds_mng_bp.route('/admin/management/rds/edit_price_point/<int:id>', methods=['POST'])
@loggedin_required()
def edit_price_point_rds(id):
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    pp = PricePointRDS.query.get_or_404(id)
    
    price_point_code = request.form.get('price_point_code')
    price_point_desc = request.form.get('price_point_desc')

    if not price_point_code or not price_point_desc:
        flash("Error: All fields are required")
        return redirect(url_for('rds_mng.admin_management_rds', _anchor='price'))

    if pp.price_point_code != price_point_code:
        existing = PricePointRDS.query.filter_by(price_point_code=price_point_code).first()
        if existing:
            flash(f"Error: Price Point Code {price_point_code} already exists")
            return redirect(url_for('rds_mng.admin_management_rds', _anchor='price'))

    try:
        pp.price_point_code = price_point_code
        pp.price_point_desc = price_point_desc
        db.session.commit()
        flash("Price Point updated successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds', _anchor='price'))

@rds_mng_bp.route('/admin/management/rds/delete-price/<int:id>', methods=['POST'])
@loggedin_required()
def delete_price_point_rds(id):
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    pp = PricePointRDS.query.get_or_404(id)
    try:
        db.session.delete(pp)
        db.session.commit()
        flash("Price Point deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")
    
    return redirect(url_for('rds_mng.admin_management_rds', _anchor='price'))
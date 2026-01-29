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

    # Check for duplicate vendor code
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

        # === MYSQL SYNC FOR TRANSACTION FORM ===
        try:
            conn = get_mysql_conn()
            if conn:
                cursor = conn.cursor()
                # Ensure it appears in the transaction dropdown for RUSTANS
                sync_qry = (
                    "INSERT INTO vendor_chain_mappings (chain_name, company_selection, vendor_code) "
                    "VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE vendor_code=%s"
                )
                cursor.execute(sync_qry, ('RUSTANS', company_name, vendor_code, vendor_code))
                conn.commit()
                conn.close()
        except Exception as e:
            # Non-critical, just log or silent fail, transaction form works via VendorRDS fallback now anyway
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
        old_vendor_code = vendor.vendor_code
        vendor.company_name = company_name
        vendor.vendor_code = vendor_code
        vendor.mfg_part_no = mfg_part_no
        
        db.session.commit()

        # === MYSQL SYNC FOR TRANSACTION FORM ===
        if old_vendor_code != vendor_code:
            try:
                conn = get_mysql_conn()
                if conn:
                    cursor = conn.cursor()
                    update_qry = "UPDATE vendor_chain_mappings SET vendor_code=%s WHERE vendor_code=%s AND chain_name='RUSTANS'"
                    cursor.execute(update_qry, (vendor_code, old_vendor_code))
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
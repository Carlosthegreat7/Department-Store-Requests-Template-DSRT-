from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import Vendor
from portal import loggedin_required
import mysql.connector


vendor_bp = Blueprint('vendor', __name__)

def get_mysql_conn():
    """Helper for raw SQL operations on the mapping table."""
    try:
        return mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="myproject"
        )
    except Exception:
        return None

@vendor_bp.route('/admin/add_vendor', methods=['GET', 'POST'])
@loggedin_required()
def add_vendor():
    """Route to add a new vendor and automatically map it to the SM chain."""
    if request.method == 'POST':
        code = request.form.get('code', '').strip().zfill(6)
        name = request.form.get('name', '').strip().upper()
        chain = request.form.get('chain', 'SM').strip().upper() 
        
        try:
            # Add to the main Vendor table (SQLAlchemy)
            new_vendor = Vendor(vendor_code=code, vendor_name=name)
            db.session.add(new_vendor)
            db.session.commit()

            # Add to the Bridge Mapping table (Raw SQL)
            conn = get_mysql_conn()
            if conn:
                cursor = conn.cursor()
                map_qry = (
                    "INSERT INTO vendor_chain_mappings (chain_name, company_selection, vendor_code) "
                    "VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE vendor_code=%s"
                )
                company_slug = name.replace(" ", "_")[:15]
                cursor.execute(map_qry, (chain, company_slug, code, code))
                conn.commit()
                conn.close()

            flash(f"Successfully added Vendor: {name} and mapped to {chain}", "success")
            return redirect(url_for('admin_management', _anchor='vendor'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Database Error: {e}", "danger")
            
    return render_template('add_vendor.html')

@vendor_bp.route('/admin/edit_vendor/<code>', methods=['GET', 'POST'])
@loggedin_required()
def edit_vendor(code):
    """Route to update vendor details and sync the mapping table."""
    vendor = Vendor.query.get_or_404(code)
    
    if request.method == 'POST':
        new_code = request.form.get('code').strip().zfill(6)
        new_name = request.form.get('name').strip().upper()
        
        try:
            # 1. Update Mapping Table First (if code is changed)
            conn = get_mysql_conn()
            if conn:
                cursor = conn.cursor()
                update_map = "UPDATE vendor_chain_mappings SET vendor_code = %s WHERE vendor_code = %s"
                cursor.execute(update_map, (new_code, code))
                conn.commit()
                conn.close()

            # 2. Update Main Vendor Table
            vendor.vendor_code = new_code
            vendor.vendor_name = new_name
            db.session.commit()
            
            flash(f"Vendor {new_name} updated and synced successfully!", "success")
            return redirect(url_for('admin_management', _anchor='vendor'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating vendor: {e}", "danger")
            
    return render_template('edit_vendor.html', vendor=vendor, old_code=code)

@vendor_bp.route('/admin/delete_vendor/<code>', methods=['POST'])
@loggedin_required()
def delete_vendor(code):
    """Route to remove vendor and its mappings."""
    vendor = Vendor.query.get(code)
    if vendor:
        try:
            # 1. Remove from Mapping Table
            conn = get_mysql_conn()
            if conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM vendor_chain_mappings WHERE vendor_code = %s", (code,))
                conn.commit()
                conn.close()

            # 2. Remove from Main Table
            db.session.delete(vendor)
            db.session.commit()
            flash(f"Vendor {code} and its mappings deleted successfully.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error deleting vendor: {e}", "danger")
    else:
        flash("Vendor not found.", "warning")
        
    return redirect(url_for('admin_management', _anchor='vendor'))